import pytest
from pydantic import ValidationError

from app.schemas.diagnosis import Diagnosis
from app.schemas.events import CustomerHistory, NormalizedEvent
from app.schemas.risk import RiskAssessment
from app.services.ai.provider import LLMProvider
from app.services.diagnosis.context_engine import compute_customer_context
from app.services.diagnosis.engine import DiagnosisEngine
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


class ScriptedProvider(LLMProvider):
    name = "scripted"

    def __init__(self, responses: list[str]):
        self._responses = list(responses)
        self.calls = 0

    def complete(self, prompt: str, timeout: float) -> str:
        self.calls += 1
        return self._responses.pop(0)


# --- Customer Context Engine (deterministic arithmetic, module D) ----------


def test_new_customer_segment():
    history = CustomerHistory(previous_successful_payments=1, previous_failed_payments=0, lifetime_value=50000)
    ctx = compute_customer_context(history)
    assert ctx.customer_segment == "new"


def test_standard_customer_segment():
    history = CustomerHistory(previous_successful_payments=5, previous_failed_payments=1, lifetime_value=200000)
    ctx = compute_customer_context(history)
    assert ctx.customer_segment == "standard"


def test_high_value_customer_by_lifetime_value():
    history = CustomerHistory(previous_successful_payments=2, previous_failed_payments=0, lifetime_value=2_000_000)
    ctx = compute_customer_context(history)
    assert ctx.customer_segment == "high_value"


def test_high_value_customer_by_payment_count():
    history = CustomerHistory(previous_successful_payments=20, previous_failed_payments=2, lifetime_value=100000)
    ctx = compute_customer_context(history)
    assert ctx.customer_segment == "high_value"


def test_failure_rate_computed_correctly():
    history = CustomerHistory(previous_successful_payments=8, previous_failed_payments=2, lifetime_value=100000)
    ctx = compute_customer_context(history)
    assert ctx.failure_rate == 0.2


def test_failure_rate_zero_with_no_history():
    history = CustomerHistory(previous_successful_payments=0, previous_failed_payments=0, lifetime_value=0)
    ctx = compute_customer_context(history)
    assert ctx.failure_rate == 0.0


def test_recovery_attempts_passed_through():
    history = CustomerHistory()
    ctx = compute_customer_context(history, recovery_attempts=2)
    assert ctx.recovery_attempts == 2


# --- Diagnosis schema structural guarantees ---------------------------------


def test_reasoning_summary_over_300_chars_is_rejected():
    with pytest.raises(ValidationError):
        Diagnosis(
            diagnosis="temporary_failure",
            confidence=0.9,
            reasoning_summary="x" * 301,
            recommended_strategy="retry_later",
        )


def test_reasoning_summary_at_300_chars_is_accepted():
    d = Diagnosis(
        diagnosis="temporary_failure",
        confidence=0.9,
        reasoning_summary="x" * 300,
        recommended_strategy="retry_later",
    )
    assert len(d.reasoning_summary) == 300


def test_diagnosis_rejects_unknown_category_value():
    with pytest.raises(ValidationError):
        Diagnosis(
            diagnosis="not_a_real_category",
            confidence=0.9,
            reasoning_summary="ok",
            recommended_strategy="retry_later",
        )


# --- DiagnosisEngine: runs fully with AI_PROVIDER=none (provider=None) -----


def _context_and_risk(event: NormalizedEvent):
    context = compute_customer_context(event.customer_history)
    risk = assess_risk(event)
    return context, risk


@pytest.mark.parametrize(
    "failure_code,expected_category",
    [
        ("NETWORK_ERROR", "temporary_failure"),
        ("GATEWAY_TIMEOUT", "temporary_failure"),
        ("INSUFFICIENT_FUNDS", "insufficient_funds"),
        ("BANK_DECLINE", "bank_decline"),
        ("ISSUER_DECLINE", "bank_decline"),
        ("CARD_EXPIRED", "expired_instrument"),
        ("INSTRUMENT_INVALID", "expired_instrument"),
        ("CHECKOUT_ABANDONED", "checkout_abandonment"),
        ("INVOICE_OVERDUE", "overdue_invoice"),
    ],
)
def test_engine_with_no_provider_classifies_each_known_failure_code(failure_code, expected_category):
    engine = DiagnosisEngine(provider=None)
    event = _event(failure_code=failure_code, attempt_number=1)
    context, risk = _context_and_risk(event)
    result = engine.diagnose(event, context, risk)

    assert result.diagnosis == expected_category
    assert result.model_provider == "fallback_rules"
    assert result.fallback_used is True
    assert result.schema_valid is True
    assert result.attempted_provider is None
    assert "AI unavailable" in result.reasoning_summary


def test_engine_with_no_provider_escalates_repeated_failures_regardless_of_code():
    engine = DiagnosisEngine(provider=None)
    event = _event(failure_code="INSUFFICIENT_FUNDS", attempt_number=4)
    context, risk = _context_and_risk(event)
    result = engine.diagnose(event, context, risk)

    assert result.diagnosis == "repeated_failure"
    assert result.confidence >= 0.85
    assert result.recommended_strategy == "escalate_human"


def test_engine_with_no_provider_returns_unknown_for_unrecognized_code():
    engine = DiagnosisEngine(provider=None)
    event = _event(failure_code="SOME_BRAND_NEW_FAILURE_CODE", attempt_number=1)
    context, risk = _context_and_risk(event)
    result = engine.diagnose(event, context, risk)

    assert result.diagnosis == "unknown"
    assert result.confidence < 0.5
    assert result.recommended_strategy == "escalate_human"


def test_engine_runs_over_all_diagnosis_categories_without_a_provider():
    """Full run with AI_PROVIDER=none equivalent (provider=None) across every category."""
    engine = DiagnosisEngine(provider=None)
    codes = [
        "NETWORK_ERROR", "INSUFFICIENT_FUNDS", "BANK_DECLINE", "CARD_EXPIRED",
        "CHECKOUT_ABANDONED", "INVOICE_OVERDUE", "TOTALLY_UNKNOWN",
    ]
    for code in codes:
        event = _event(failure_code=code, attempt_number=1)
        context, risk = _context_and_risk(event)
        result = engine.diagnose(event, context, risk)
        assert result.model_provider == "fallback_rules"
        assert 0.0 <= result.confidence <= 1.0


# --- DiagnosisEngine: AI available but returns malformed output ------------


def test_engine_falls_back_when_provider_returns_malformed_json_twice():
    provider = ScriptedProvider(["not json {{{", "still not json"])
    engine = DiagnosisEngine(provider=provider)
    event = _event(failure_code="BANK_DECLINE", attempt_number=1)
    context, risk = _context_and_risk(event)

    result = engine.diagnose(event, context, risk)

    assert result.fallback_used is True
    assert result.schema_valid is False  # the AI's own attempt was invalid
    assert result.model_provider == "fallback_rules"  # what actually produced this diagnosis
    assert result.attempted_provider == "scripted"  # what we tried first
    assert result.diagnosis == "bank_decline"  # fallback still classifies correctly
    assert provider.calls == 2  # exactly one retry


def test_engine_falls_back_when_provider_returns_schema_violating_json():
    # Valid JSON, but missing required fields / wrong category value.
    bad_shape = '{"diagnosis": "not_a_real_category", "confidence": 1.5}'
    provider = ScriptedProvider([bad_shape, bad_shape])
    engine = DiagnosisEngine(provider=provider)
    event = _event(failure_code="INSUFFICIENT_FUNDS", attempt_number=1)
    context, risk = _context_and_risk(event)

    result = engine.diagnose(event, context, risk)

    assert result.fallback_used is True
    assert result.schema_valid is False
    assert result.diagnosis == "insufficient_funds"


def test_engine_succeeds_on_first_valid_ai_response():
    valid = (
        '{"diagnosis": "temporary_failure", "confidence": 0.88, '
        '"reasoning_summary": "Recent failure looks transient given retry history.", '
        '"recommended_strategy": "retry_later"}'
    )
    provider = ScriptedProvider([valid])
    engine = DiagnosisEngine(provider=provider)
    event = _event(failure_code="NETWORK_ERROR", attempt_number=1)
    context, risk = _context_and_risk(event)

    result = engine.diagnose(event, context, risk)

    assert result.fallback_used is False
    assert result.schema_valid is True
    assert result.model_provider == "scripted"
    assert result.diagnosis == "temporary_failure"
    assert result.confidence == 0.88
    assert provider.calls == 1


def test_engine_recovers_on_retry_after_one_malformed_response():
    valid = (
        '{"diagnosis": "bank_decline", "confidence": 0.7, '
        '"reasoning_summary": "Issuer declined the transaction.", '
        '"recommended_strategy": "escalate_or_stop"}'
    )
    provider = ScriptedProvider(["garbage output", valid])
    engine = DiagnosisEngine(provider=provider)
    event = _event(failure_code="BANK_DECLINE", attempt_number=1)
    context, risk = _context_and_risk(event)

    result = engine.diagnose(event, context, risk)

    assert result.fallback_used is False
    assert result.diagnosis == "bank_decline"
    assert provider.calls == 2
