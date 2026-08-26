import random

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401 -- registers all tables
from app.db.session import Base
from app.models.audit import AuditLog
from app.models.customer import Customer
from app.models.payment import Payment
from app.models.recovery import AIAnalysis, RecoveryAction, RecoveryCase
from app.schemas.events import CustomerHistory, NormalizedEvent
from app.services.diagnosis.engine import DiagnosisEngine
from app.services.payment_gateway.simulator import RecoverySimulator
from app.services.pipeline.orchestrator import run_pipeline_for_event
from app.services.recovery_agent.agent import RecoveryAgent


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def _event(**overrides) -> NormalizedEvent:
    base = dict(
        event_id="evt_demo_1",
        customer_id="cust_demo_1",
        payment_id="pay_demo_1",
        amount=1299900,  # ₹12,999 -- matches the spec §22 demo scenario
        currency="INR",
        status="failed",
        failure_code="NETWORK_ERROR",
        failure_reason="Temporary network/bank failure",
        timestamp="2026-08-20T10:00:00Z",
        payment_method="card",
        attempt_number=1,
        customer_history=CustomerHistory(
            previous_successful_payments=20, previous_failed_payments=1, lifetime_value=5_000_000
        ),
    )
    base.update(overrides)
    return NormalizedEvent(**base)


class AlwaysSucceedRandom(random.Random):
    def random(self):
        return 0.0


class AlwaysFailRandom(random.Random):
    def random(self):
        return 0.999999


def _stages(db_session, payment_id) -> list[str]:
    rows = (
        db_session.query(AuditLog)
        .filter(AuditLog.payment_id == payment_id)
        .order_by(AuditLog.timestamp.asc())
        .all()
    )
    return [r.stage for r in rows]


# --- Spec §22 demo scenario 1: temporary failure, successful recovery ------


def test_success_scenario_produces_the_exact_documented_audit_chain(db_session):
    event = _event()
    diagnosis_engine = DiagnosisEngine(provider=None)
    recovery_agent = RecoveryAgent(provider=None)
    gateway = RecoverySimulator(rng=AlwaysSucceedRandom())

    result = run_pipeline_for_event(
        db_session, event, diagnosis_engine=diagnosis_engine, recovery_agent=recovery_agent, gateway=gateway
    )

    stages = _stages(db_session, result["payment_id"])
    assert stages == [
        "PAYMENT_FAILED",
        "RISK_DETECTED",
        "AI_DIAGNOSED",
        "AI_FALLBACK_USED",  # provider=None -> fallback rules used for diagnosis
        "ACTION_PROPOSED",
        "POLICY_APPROVED",
        "ACTION_EXECUTED",
        "PAYMENT_RECOVERED",
    ]
    assert result["case_status"] == "recovered"
    assert result["revenue_recovered"] == 1299900

    payment = db_session.get(Payment, result["payment_id"])
    assert payment.status.value == "recovered"


# --- Spec §22 demo scenario 2: repeated failures, policy blocks, escalates -


def test_repeated_failure_scenario_is_caught_by_the_agent_and_escalates(db_session):
    """
    With attempt_number=3, the diagnosis fallback classifies this as
    repeated_failure, and the recovery agent's own fallback already proposes
    ESCALATE_HUMAN for that category -- so policy sees a proposal it can
    simply approve (POLICY_APPROVED, not POLICY_BLOCKED). This is the agent
    catching the problem before policy needs to. See the separate test below
    for a case where policy has to actually redirect a bad proposal.
    """
    event = _event(
        payment_id="pay_demo_2",
        event_id="evt_demo_2",
        customer_id="cust_demo_2",
        amount=1_800_000,  # ₹18,000
        failure_code="BANK_DECLINE",
        attempt_number=3,  # 3 repeated failures per the demo scenario
    )
    diagnosis_engine = DiagnosisEngine(provider=None)
    recovery_agent = RecoveryAgent(provider=None)
    gateway = RecoverySimulator(rng=AlwaysFailRandom())

    result = run_pipeline_for_event(
        db_session, event, diagnosis_engine=diagnosis_engine, recovery_agent=recovery_agent, gateway=gateway
    )

    stages = _stages(db_session, result["payment_id"])
    assert stages == [
        "PAYMENT_FAILED",
        "RISK_DETECTED",
        "AI_DIAGNOSED",
        "AI_FALLBACK_USED",
        "ACTION_PROPOSED",
        "POLICY_APPROVED",  # agent already proposed ESCALATE_HUMAN; policy just confirms it
        "ESCALATED",
    ]
    assert result["case_status"] == "escalated"
    assert result["final_action"] == "ESCALATE_HUMAN"
    # Nothing was executed against the gateway -- no ACTION_EXECUTED, no money moved.
    assert "ACTION_EXECUTED" not in stages
    assert "PAYMENT_RECOVERED" not in stages


def test_policy_blocks_and_redirects_when_ai_proposes_an_unsafe_retry(db_session):
    """
    This is the case where policy itself has to do the catching: a scripted
    "AI" proposes RETRY_PAYMENT despite three prior failures and a
    repeated_failure diagnosis. The agent didn't self-correct (simulating a
    misbehaving model), so POLICY_BLOCKED must appear and the final action
    must be redirected to ESCALATE_HUMAN regardless of what was proposed.
    """
    from app.services.ai.provider import LLMProvider

    class BadProvider(LLMProvider):
        name = "bad_ai"

        def complete(self, prompt: str, timeout: float) -> str:
            return (
                '{"action": "RETRY_PAYMENT", "priority": "high", '
                '"reason": "Retrying anyway.", "expected_recovery_value": 500000, "confidence": 0.9}'
            )

    event = _event(
        payment_id="pay_demo_3",
        event_id="evt_demo_3",
        customer_id="cust_demo_3",
        amount=500000,
        failure_code="BANK_DECLINE",
        attempt_number=3,
    )
    diagnosis_engine = DiagnosisEngine(provider=None)  # diagnosis still falls back -> repeated_failure
    recovery_agent = RecoveryAgent(provider=BadProvider())  # but the "AI" proposes RETRY_PAYMENT anyway
    gateway = RecoverySimulator(rng=AlwaysFailRandom())

    result = run_pipeline_for_event(
        db_session, event, diagnosis_engine=diagnosis_engine, recovery_agent=recovery_agent, gateway=gateway
    )

    stages = _stages(db_session, result["payment_id"])
    assert "POLICY_BLOCKED" in stages
    assert stages[stages.index("POLICY_BLOCKED") + 1] == "ESCALATED"
    assert result["final_action"] == "ESCALATE_HUMAN"
    assert "ACTION_EXECUTED" not in stages  # the unsafe RETRY_PAYMENT never reached the gateway


# --- Duplicate event handling -------------------------------------------------


def test_duplicate_event_only_logs_once_and_creates_no_second_case(db_session):
    event = _event()
    diagnosis_engine = DiagnosisEngine(provider=None)
    recovery_agent = RecoveryAgent(provider=None)
    gateway = RecoverySimulator(rng=AlwaysSucceedRandom())

    result1 = run_pipeline_for_event(
        db_session, event, diagnosis_engine=diagnosis_engine, recovery_agent=recovery_agent, gateway=gateway
    )
    stages_after_first = _stages(db_session, result1["payment_id"])

    # Re-submit the exact same event (same event_id) -- must be ignored.
    result2 = run_pipeline_for_event(
        db_session, event, diagnosis_engine=diagnosis_engine, recovery_agent=recovery_agent, gateway=gateway
    )
    stages_after_second = _stages(db_session, result1["payment_id"])

    assert result2["ignored"] is True
    assert result2["payment_id"] == result1["payment_id"]
    # Exactly one new row (DUPLICATE_IGNORED) was added, nothing else re-ran.
    assert stages_after_second == stages_after_first + ["DUPLICATE_IGNORED"]

    # No second Payment, Customer, or RecoveryCase was created.
    assert db_session.query(Payment).count() == 1
    assert db_session.query(Customer).count() == 1
    assert db_session.query(RecoveryCase).count() == 1


def test_duplicate_event_does_not_re_execute_or_change_revenue_recovered(db_session):
    event = _event()
    diagnosis_engine = DiagnosisEngine(provider=None)
    recovery_agent = RecoveryAgent(provider=None)
    gateway = RecoverySimulator(rng=AlwaysSucceedRandom())

    run_pipeline_for_event(db_session, event, diagnosis_engine=diagnosis_engine, recovery_agent=recovery_agent, gateway=gateway)
    action_after_first = db_session.query(RecoveryAction).one()
    revenue_after_first = action_after_first.revenue_recovered
    assert revenue_after_first == 1299900

    run_pipeline_for_event(db_session, event, diagnosis_engine=diagnosis_engine, recovery_agent=recovery_agent, gateway=gateway)

    # Still only one RecoveryAction row -- nothing executed a second time.
    assert db_session.query(RecoveryAction).count() == 1
    action_after_second = db_session.query(RecoveryAction).one()
    assert action_after_second.id == action_after_first.id
    assert action_after_second.revenue_recovered == revenue_after_first


# --- Persistence correctness --------------------------------------------------


def test_ai_analyses_persisted_for_both_diagnosis_and_proposal_stages(db_session):
    event = _event()
    diagnosis_engine = DiagnosisEngine(provider=None)
    recovery_agent = RecoveryAgent(provider=None)
    gateway = RecoverySimulator(rng=AlwaysSucceedRandom())

    run_pipeline_for_event(db_session, event, diagnosis_engine=diagnosis_engine, recovery_agent=recovery_agent, gateway=gateway)

    analyses = db_session.query(AIAnalysis).all()
    stages = {a.stage.value for a in analyses}
    assert stages == {"diagnosis", "action_proposal"}


def test_failed_execution_below_max_attempts_leaves_case_open_for_retry(db_session):
    # Amount kept below the high-value threshold so the value-threshold policy
    # rule doesn't also fire and confound what this test is checking.
    event = _event(payment_id="pay_demo_low_value", event_id="evt_demo_low_value", amount=300000, failure_code="INSUFFICIENT_FUNDS")
    diagnosis_engine = DiagnosisEngine(provider=None)
    recovery_agent = RecoveryAgent(provider=None)
    gateway = RecoverySimulator(rng=AlwaysFailRandom())

    result = run_pipeline_for_event(
        db_session, event, diagnosis_engine=diagnosis_engine, recovery_agent=recovery_agent, gateway=gateway
    )

    assert result["case_status"] == "open"  # first failed attempt, under max_recovery_attempts=3
    case = db_session.query(RecoveryCase).filter(RecoveryCase.payment_id == result["payment_id"]).first()
    assert case.recovery_attempts == 1
