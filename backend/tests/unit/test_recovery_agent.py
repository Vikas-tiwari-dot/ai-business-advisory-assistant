import ast
import inspect

import pytest
from pydantic import ValidationError

from app.schemas.context import CustomerContext
from app.schemas.diagnosis import DiagnosisResult
from app.schemas.events import CustomerHistory, NormalizedEvent
from app.schemas.recovery_action import ProposedAction
from app.services.ai.provider import LLMProvider
from app.services.recovery_agent import agent as agent_module
from app.services.recovery_agent.agent import RecoveryAgent
from app.services.risk_detector.detector import assess_risk


def _event(**overrides) -> NormalizedEvent:
    base = dict(
        event_id="evt_1",
        customer_id="cust_1",
        payment_id="pay_1",
        amount=499900,
        currency="INR",
        status="failed",
        failure_code="NETWORK_ERROR",
        failure_reason="Temporary network error",
        timestamp="2026-08-20T10:00:00Z",
        payment_method="card",
        attempt_number=1,
        customer_history=CustomerHistory(),
    )
    base.update(overrides)
    return NormalizedEvent(**base)


def _diagnosis(**overrides) -> DiagnosisResult:
    base = dict(
        diagnosis="temporary_failure",
        confidence=0.85,
        reasoning_summary="Looks transient.",
        recommended_strategy="retry_later",
        model_provider="fallback_rules",
        schema_valid=True,
        fallback_used=True,
        attempted_provider=None,
    )
    base.update(overrides)
    return DiagnosisResult(**base)


def _context(**overrides) -> CustomerContext:
    base = dict(customer_segment="standard", lifetime_value=200000, failure_rate=0.1, recovery_attempts=0)
    base.update(overrides)
    return CustomerContext(**base)


class ScriptedProvider(LLMProvider):
    name = "scripted"

    def __init__(self, responses: list[str]):
        self._responses = list(responses)
        self.calls = 0

    def complete(self, prompt: str, timeout: float) -> str:
        self.calls += 1
        return self._responses.pop(0)


# --- Structural boundary: the agent cannot import execution/DB/gateway code -


def test_agent_module_has_no_forbidden_imports():
    """
    This is the Phase 6 exit criterion: RecoveryAgent must not be able to
    import the payment gateway, the DB session, SQLAlchemy, or the audit
    writer. If a future edit adds one of these imports, this test fails
    immediately rather than the boundary silently eroding.
    """
    tree = ast.parse(inspect.getsource(agent_module))
    imported_modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.add(node.module)

    forbidden_prefixes = (
        "app.services.payment_gateway",
        "app.db",
        "app.services.audit",
        "sqlalchemy",
        "razorpay",
        "httpx",  # no direct network calls from the agent itself -- only via LLMProvider
    )
    violations = [
        m for m in imported_modules if any(m == p or m.startswith(p + ".") for p in forbidden_prefixes)
    ]
    assert not violations, f"RecoveryAgent imports forbidden modules: {violations}"


def test_agent_module_source_never_mentions_execution_terms():
    source = inspect.getsource(agent_module)
    forbidden_terms = ["session.commit", "gateway.charge", "razorpay.Client", "INSERT INTO", "UPDATE payments"]
    for term in forbidden_terms:
        assert term not in source


# --- Schema enforcement: only the six actions are ever valid ---------------


def test_proposed_action_rejects_arbitrary_action_value():
    with pytest.raises(ValidationError):
        ProposedAction(
            action="TRANSFER_FUNDS",  # not in the closed set
            priority="high",
            reason="not allowed",
            expected_recovery_value=100,
            confidence=0.9,
        )


def test_proposed_action_reason_capped_at_300_chars():
    with pytest.raises(ValidationError):
        ProposedAction(
            action="STOP",
            priority="low",
            reason="x" * 301,
            expected_recovery_value=0,
            confidence=0.9,
        )


# --- Fallback logic (AI_PROVIDER=none, provider=None) ----------------------


def test_fallback_stops_for_opted_out_customer_regardless_of_diagnosis():
    agent = RecoveryAgent(provider=None)
    event = _event(customer_history=CustomerHistory(opted_out=True))
    risk = assess_risk(event)
    result = agent.propose(event, _context(), _diagnosis(diagnosis="temporary_failure", confidence=0.95), risk)

    assert result.action == "STOP"
    assert result.expected_recovery_value == 0
    assert result.fallback_used is True
    assert result.model_provider == "fallback_rules"


def test_fallback_escalates_on_low_confidence_diagnosis():
    agent = RecoveryAgent(provider=None)
    event = _event()
    risk = assess_risk(event)
    low_conf_diagnosis = _diagnosis(diagnosis="temporary_failure", confidence=0.10)
    result = agent.propose(event, _context(), low_conf_diagnosis, risk)

    assert result.action == "ESCALATE_HUMAN"


def test_fallback_escalates_when_max_recovery_attempts_reached():
    agent = RecoveryAgent(provider=None)
    event = _event()
    risk = assess_risk(event)
    context = _context(recovery_attempts=3)  # matches default MAX_RECOVERY_ATTEMPTS=3
    result = agent.propose(event, context, _diagnosis(confidence=0.9), risk)

    assert result.action == "ESCALATE_HUMAN"
    assert "attempts already made" in result.reason


@pytest.mark.parametrize(
    "category,expected_action",
    [
        ("temporary_failure", "RETRY_PAYMENT"),
        ("insufficient_funds", "SCHEDULE_RETRY"),
        ("bank_decline", "ESCALATE_HUMAN"),
        ("expired_instrument", "OFFER_ALTERNATE_METHOD"),
        ("repeated_failure", "ESCALATE_HUMAN"),
        ("checkout_abandonment", "SEND_REMINDER"),
        ("overdue_invoice", "SEND_REMINDER"),
        ("unknown", "ESCALATE_HUMAN"),
    ],
)
def test_fallback_maps_each_diagnosis_category_to_expected_action(category, expected_action):
    agent = RecoveryAgent(provider=None)
    event = _event()
    risk = assess_risk(event)
    diagnosis = _diagnosis(diagnosis=category, confidence=0.85)  # above low-confidence threshold
    result = agent.propose(event, _context(), diagnosis, risk)

    assert result.action == expected_action


def test_fallback_expected_recovery_value_zero_for_stop():
    agent = RecoveryAgent(provider=None)
    event = _event(customer_history=CustomerHistory(opted_out=True))
    risk = assess_risk(event)
    result = agent.propose(event, _context(), _diagnosis(confidence=0.9), risk)
    assert result.action == "STOP"
    assert result.expected_recovery_value == 0


def test_fallback_priority_high_for_high_value_segment():
    agent = RecoveryAgent(provider=None)
    event = _event()
    risk = assess_risk(event)
    context = _context(customer_segment="high_value")
    result = agent.propose(event, context, _diagnosis(confidence=0.85), risk)
    assert result.priority == "high"


# --- AI path: succeeds, and falls back on malformed output ------------------


def test_agent_uses_ai_output_when_valid():
    valid = (
        '{"action": "RETRY_PAYMENT", "priority": "high", '
        '"reason": "Transient failure, strong payment history.", '
        '"expected_recovery_value": 499900, "confidence": 0.86}'
    )
    provider = ScriptedProvider([valid])
    agent = RecoveryAgent(provider=provider)
    event = _event()
    risk = assess_risk(event)
    result = agent.propose(event, _context(), _diagnosis(confidence=0.9), risk)

    assert result.fallback_used is False
    assert result.action == "RETRY_PAYMENT"
    assert result.model_provider == "scripted"
    assert provider.calls == 1


def test_agent_falls_back_when_ai_proposes_action_outside_closed_set():
    # The AI tries to invent an action that isn't in the six allowed -- this
    # must fail schema validation and trigger the deterministic fallback.
    invalid_action = '{"action": "SEND_MONEY_DIRECTLY", "priority": "high", "reason": "x", "expected_recovery_value": 100, "confidence": 0.9}'
    provider = ScriptedProvider([invalid_action, invalid_action])
    agent = RecoveryAgent(provider=provider)
    event = _event()
    risk = assess_risk(event)
    result = agent.propose(event, _context(), _diagnosis(confidence=0.9), risk)

    assert result.fallback_used is True
    assert result.schema_valid is False
    assert result.action in {
        "RETRY_PAYMENT", "SEND_REMINDER", "OFFER_ALTERNATE_METHOD",
        "SCHEDULE_RETRY", "ESCALATE_HUMAN", "STOP",
    }  # always lands back in the closed set


def test_agent_falls_back_on_malformed_json_after_one_retry():
    provider = ScriptedProvider(["not json", "still not json"])
    agent = RecoveryAgent(provider=provider)
    event = _event()
    risk = assess_risk(event)
    result = agent.propose(event, _context(), _diagnosis(confidence=0.9), risk)

    assert result.fallback_used is True
    assert result.schema_valid is False
    assert result.attempted_provider == "scripted"
    assert result.model_provider == "fallback_rules"
    assert provider.calls == 2


def test_agent_recovers_on_retry():
    valid = '{"action": "SCHEDULE_RETRY", "priority": "medium", "reason": "ok", "expected_recovery_value": 499900, "confidence": 0.7}'
    provider = ScriptedProvider(["garbage", valid])
    agent = RecoveryAgent(provider=provider)
    event = _event()
    risk = assess_risk(event)
    result = agent.propose(event, _context(), _diagnosis(confidence=0.9), risk)

    assert result.fallback_used is False
    assert result.action == "SCHEDULE_RETRY"
    assert provider.calls == 2
