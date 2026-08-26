"""
Failure injection suite (spec §10). Each test here reproduces one specific
failure scenario and asserts the system degrades gracefully -- no crash, no
silently-dropped state, and (where applicable) a clear audit trail entry
explaining what happened. This file exists to make all 7 scenarios
independently demoable, not just theoretically handled.

Scenario -> where it's tested:
  1. Razorpay API timeout          -> this file, test_razorpay_gateway_timeout*
  2. Malformed webhook             -> tests/integration/test_webhook_api.py
  3. Duplicate webhook             -> tests/integration/test_webhook_api.py,
                                       tests/unit/test_orchestrator.py
  4. LLM timeout                   -> this file, test_llm_timeout*
  5. Invalid LLM JSON              -> tests/unit/test_ai_structured.py,
                                       tests/unit/test_diagnosis_engine.py,
                                       tests/unit/test_recovery_agent.py
  6. Database failure simulation   -> this file, test_database_write_failure*
  7. Payment retry failure         -> this file, test_payment_retry_exhaustion*
"""
import random

import httpx
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401
from app.core.exceptions import DatabaseWriteError
from app.db.session import Base
from app.schemas.diagnosis import DiagnosisResult
from app.schemas.events import CustomerHistory, NormalizedEvent
from app.services.ai.provider import LLMProvider
from app.services.diagnosis.engine import DiagnosisEngine
from app.services.payment_gateway.razorpay_gateway import RazorpayTestModeGateway
from app.services.payment_gateway.simulator import RecoverySimulator
from app.services.pipeline.orchestrator import _commit_with_retry, run_pipeline_for_event
from app.services.recovery_agent.agent import RecoveryAgent


@pytest.fixture
def db_session():
    engine_ = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine_)
    Session = sessionmaker(bind=engine_)
    session = Session()
    yield session
    session.close()


def _event(**overrides) -> NormalizedEvent:
    base = dict(
        event_id="evt_fail_1",
        customer_id="cust_fail_1",
        payment_id="pay_fail_1",
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


class AlwaysFailRandom(random.Random):
    def random(self):
        return 0.999999


# =============================================================================
# 1. Razorpay API timeout
# =============================================================================


def test_razorpay_gateway_timeout_returns_failed_result_not_a_crash(monkeypatch):
    def _raise_timeout(*args, **kwargs):
        raise httpx.TimeoutException("simulated Razorpay Test Mode timeout")

    monkeypatch.setattr(httpx, "post", _raise_timeout)

    gateway = RazorpayTestModeGateway(key_id="rzp_test_x", key_secret="secret_x")
    event = _event()
    diagnosis = DiagnosisResult(
        diagnosis="temporary_failure", confidence=0.8, reasoning_summary="x",
        recommended_strategy="retry_later", model_provider="fallback_rules",
        schema_valid=True, fallback_used=True,
    )

    result = gateway.execute("RETRY_PAYMENT", event, diagnosis, revenue_at_risk=499900)

    assert result.executed is False
    assert result.result == "failed"
    assert result.revenue_recovered == 0
    assert "timeout" in result.message.lower() or "Timeout" in result.message


def test_razorpay_gateway_connection_error_also_handled_gracefully(monkeypatch):
    def _raise_connect_error(*args, **kwargs):
        raise httpx.ConnectError("simulated DNS/connection failure")

    monkeypatch.setattr(httpx, "post", _raise_connect_error)

    gateway = RazorpayTestModeGateway(key_id="rzp_test_x", key_secret="secret_x")
    diagnosis = DiagnosisResult(
        diagnosis="temporary_failure", confidence=0.8, reasoning_summary="x",
        recommended_strategy="retry_later", model_provider="fallback_rules",
        schema_valid=True, fallback_used=True,
    )
    result = gateway.execute("SCHEDULE_RETRY", _event(), diagnosis, revenue_at_risk=100000)
    assert result.result == "failed"
    assert result.executed is False  # never silently reported as success


# =============================================================================
# 4. LLM timeout (end-to-end through the orchestrator, not just call_structured)
# =============================================================================


class TimeoutProvider(LLMProvider):
    name = "timeout_provider"

    def complete(self, prompt: str, timeout: float) -> str:
        raise TimeoutError(f"simulated LLM timeout after {timeout}s")


def test_llm_timeout_falls_back_and_pipeline_completes_successfully(db_session):
    """
    Spec §10's documented recovery path:
        LLM unavailable -> Fallback deterministic rules -> Continue safe workflow
    Verified end-to-end through the real orchestrator, not just the isolated
    diagnosis engine -- the whole pipeline must still produce a result.
    """
    diagnosis_engine = DiagnosisEngine(provider=TimeoutProvider())
    recovery_agent = RecoveryAgent(provider=TimeoutProvider())
    gateway = RecoverySimulator(rng=random.Random(1))

    result = run_pipeline_for_event(
        db_session, _event(),
        diagnosis_engine=diagnosis_engine, recovery_agent=recovery_agent, gateway=gateway,
    )

    assert result["payment_id"] is not None  # pipeline completed, didn't crash

    from app.models.audit import AuditLog
    stages = [
        r.stage for r in db_session.query(AuditLog).filter(AuditLog.payment_id == result["payment_id"]).all()
    ]
    assert "AI_FALLBACK_USED" in stages

    from app.models.recovery import AIAnalysis
    diagnosis_row = db_session.query(AIAnalysis).filter(AIAnalysis.stage == "diagnosis").first()
    assert diagnosis_row.model_provider == "fallback_rules"
    assert "AI unavailable" in diagnosis_row.reasoning_summary


def test_llm_timeout_does_not_prevent_a_successful_recovery(db_session):
    diagnosis_engine = DiagnosisEngine(provider=TimeoutProvider())
    recovery_agent = RecoveryAgent(provider=TimeoutProvider())

    class AlwaysSucceedRandom(random.Random):
        def random(self):
            return 0.0

    gateway = RecoverySimulator(rng=AlwaysSucceedRandom())

    result = run_pipeline_for_event(
        db_session, _event(),
        diagnosis_engine=diagnosis_engine, recovery_agent=recovery_agent, gateway=gateway,
    )
    assert result["case_status"] == "recovered"
    assert result["revenue_recovered"] == 499900


# =============================================================================
# 6. Database failure simulation
# =============================================================================


def test_commit_retry_succeeds_after_one_transient_failure(db_session):
    """Simulates a transient DB error (e.g. a lock timeout) that clears on retry."""
    real_commit = db_session.commit
    call_count = {"n": 0}

    def _flaky_commit():
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise OperationalError("simulated lock timeout", {}, Exception("locked"))
        return real_commit()

    db_session.commit = _flaky_commit

    # Should not raise -- succeeds on the second internal attempt.
    _commit_with_retry(db_session, attempts=2)
    assert call_count["n"] == 2


def test_commit_retry_raises_typed_error_after_exhausting_attempts(db_session):
    def _always_fail():
        raise OperationalError("simulated persistent DB outage", {}, Exception("down"))

    db_session.commit = _always_fail
    db_session.rollback = lambda: None  # no-op for this test

    with pytest.raises(DatabaseWriteError):
        _commit_with_retry(db_session, attempts=2)


def test_database_failure_during_pipeline_raises_clear_error_not_raw_sqlalchemy_exception(db_session):
    """
    End-to-end: if the database is genuinely down for the whole request, the
    orchestrator must surface a typed, catchable error rather than letting a
    raw SQLAlchemy exception (or worse, an unhandled crash) propagate.
    """
    def _always_fail():
        raise OperationalError("simulated total DB outage", {}, Exception("down"))

    db_session.commit = _always_fail
    db_session.rollback = lambda: None

    diagnosis_engine = DiagnosisEngine(provider=None)
    recovery_agent = RecoveryAgent(provider=None)
    gateway = RecoverySimulator(rng=random.Random(1))

    with pytest.raises(DatabaseWriteError):
        run_pipeline_for_event(
            db_session, _event(),
            diagnosis_engine=diagnosis_engine, recovery_agent=recovery_agent, gateway=gateway,
        )


def test_database_failure_surfaces_as_503_through_the_live_api(tmp_path):
    """
    Same failure, but exercised through the real FastAPI app + TestClient --
    confirms the RazorRecoverError exception handler in app/main.py actually
    catches the DatabaseWriteError subclass and turns it into a clean 503
    envelope, not a generic 500. This is the reproducible demo trigger for
    scenario 6 from the API surface, not just the internal service layer.
    """
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine as _create_engine
    from sqlalchemy.orm import sessionmaker as _sessionmaker

    from app.db.session import get_db
    from app.main import app

    db_path = tmp_path / "test_db_failure_api.db"
    engine_ = _create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(bind=engine_)
    TestingSessionLocal = _sessionmaker(bind=engine_)

    def override_get_db():
        db = TestingSessionLocal()

        def _always_fail():
            raise OperationalError("simulated DB outage", {}, Exception("down"))

        db.commit = _always_fail
        db.rollback = lambda: None
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app, raise_server_exceptions=False)

    event_payload = {
        "event_id": "evt_db_fail_api", "customer_id": "cust_api", "payment_id": "pay_api",
        "amount": 100000, "currency": "INR", "status": "failed",
        "failure_code": "NETWORK_ERROR", "failure_reason": "x",
        "timestamp": "2026-08-20T10:00:00Z", "payment_method": "card", "attempt_number": 1,
        "customer_history": {
            "previous_successful_payments": 0, "previous_failed_payments": 0,
            "lifetime_value": 0, "opted_out": False,
        },
    }
    try:
        resp = client.post("/api/events/payment", json=event_payload)
        assert resp.status_code == 503
        body = resp.json()
        assert body["error"]["code"] == "DATABASE_WRITE_ERROR"
    finally:
        app.dependency_overrides.clear()


# =============================================================================
# 7. Payment retry failure (exhausting max attempts -> unrecoverable)
# =============================================================================


def test_repeated_retry_failures_eventually_mark_payment_unrecoverable(db_session):
    """
    Simulates three separate recovery attempts against the same payment (as
    would happen via three webhook deliveries or three batch re-runs), each
    of which fails at the gateway. After the configured max_recovery_attempts
    is reached, the payment must be marked unrecoverable and the case closed
    -- not retried forever.
    """
    diagnosis_engine = DiagnosisEngine(provider=None)
    recovery_agent = RecoveryAgent(provider=None)
    gateway = RecoverySimulator(rng=AlwaysFailRandom())

    # Use insufficient_funds (SCHEDULE_RETRY) at a low amount so it doesn't
    # trip the high-value threshold and get escalated instead of retried.
    for i in range(3):
        result = run_pipeline_for_event(
            db_session,
            _event(event_id=f"evt_retry_{i}", amount=100000, failure_code="INSUFFICIENT_FUNDS"),
            diagnosis_engine=diagnosis_engine, recovery_agent=recovery_agent, gateway=gateway,
        )

    assert result["case_status"] == "closed_unrecovered"

    from app.models.payment import Payment
    payment = db_session.get(Payment, result["payment_id"])
    assert payment.status.value == "unrecoverable"

    from app.models.recovery import RecoveryCase
    case = db_session.query(RecoveryCase).filter(RecoveryCase.payment_id == payment.id).first()
    assert case.recovery_attempts == 3
    assert case.closed_at is not None


def test_ai_fallback_used_logged_only_once_when_only_action_proposal_falls_back(db_session):
    """
    Covers the branch where diagnosis succeeds via a real AI response but the
    RecoveryAgent's own AI call fails and falls back -- exactly one
    AI_FALLBACK_USED entry should appear (tagged for the action_proposal
    stage), not zero and not a duplicate from the diagnosis stage which
    didn't actually fall back.
    """
    from app.services.ai.provider import LLMProvider

    class WorkingDiagnosisProvider(LLMProvider):
        name = "working_diagnosis"

        def complete(self, prompt: str, timeout: float) -> str:
            return (
                '{"diagnosis": "temporary_failure", "confidence": 0.9, '
                '"reasoning_summary": "Real AI diagnosis.", "recommended_strategy": "retry_later"}'
            )

    class BrokenAgentProvider(LLMProvider):
        name = "broken_agent"

        def complete(self, prompt: str, timeout: float) -> str:
            raise RuntimeError("simulated agent provider failure")

    diagnosis_engine = DiagnosisEngine(provider=WorkingDiagnosisProvider())
    recovery_agent = RecoveryAgent(provider=BrokenAgentProvider())
    gateway = RecoverySimulator(rng=random.Random(1))

    result = run_pipeline_for_event(
        db_session, _event(),
        diagnosis_engine=diagnosis_engine, recovery_agent=recovery_agent, gateway=gateway,
    )

    from app.models.audit import AuditLog

    stages = [
        r.stage for r in db_session.query(AuditLog).filter(AuditLog.payment_id == result["payment_id"]).all()
    ]
    assert stages.count("AI_FALLBACK_USED") == 1  # exactly one, for the agent stage only


def test_payment_stays_open_before_exhausting_max_attempts(db_session):
    diagnosis_engine = DiagnosisEngine(provider=None)
    recovery_agent = RecoveryAgent(provider=None)
    gateway = RecoverySimulator(rng=AlwaysFailRandom())

    result = run_pipeline_for_event(
        db_session,
        _event(amount=100000, failure_code="INSUFFICIENT_FUNDS"),
        diagnosis_engine=diagnosis_engine, recovery_agent=recovery_agent, gateway=gateway,
    )
    assert result["case_status"] == "open"  # only 1 of 3 allowed attempts used
