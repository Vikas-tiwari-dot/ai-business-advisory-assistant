"""
Pipeline orchestrator. Not one of the folders named in docs/architecture.md's
original folder-structure section -- flagging that deviation explicitly. It
exists because something has to call the six independently-built services
(risk detector, context engine, diagnosis engine, recovery agent, policy
engine, payment gateway) in order, persist the results, and drive the audit
chain described in spec §9:

    PAYMENT_FAILED -> RISK_DETECTED -> AI_DIAGNOSED -> ACTION_PROPOSED ->
    POLICY_APPROVED|POLICY_BLOCKED -> ACTION_EXECUTED -> PAYMENT_RECOVERED
                                                       -> ESCALATED (if applicable)

This module is the natural home for that glue. The API layer (later phases)
will call `run_pipeline_for_event` from the `/api/recovery/*` endpoints rather
than reimplementing this sequencing.
"""
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.exceptions import DatabaseWriteError
from app.models.audit import AuditLog
from app.models.customer import Customer
from app.models.enums import (
    AIStage,
    AuditStage,
    CustomerSegment,
    DatasetSplit,
    EventSource,
)
from app.models.enums import PaymentMethod as ORMPaymentMethod
from app.models.enums import PaymentStatus as ORMPaymentStatus
from app.models.enums import RecoveryActionType as ORMRecoveryActionType
from app.models.enums import RecoveryCaseStatus
from app.models.payment import Payment, PaymentAttempt
from app.models.recovery import AIAnalysis, RecoveryAction, RecoveryCase
from app.schemas.events import NormalizedEvent
from app.services.audit.logger import AuditLogger
from app.services.diagnosis.context_engine import compute_customer_context
from app.services.diagnosis.engine import DiagnosisEngine
from app.services.payment_gateway.gateway import PaymentGateway
from app.services.policy_engine.engine import evaluate as evaluate_policy
from app.services.recovery_agent.agent import RecoveryAgent
from app.services.risk_detector.detector import assess_risk


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _commit_with_retry(db: Session, *, attempts: int = 2) -> None:
    """
    Spec §10's "database failure simulation": a transient DB write failure
    (connection blip, lock timeout, etc.) shouldn't crash the request or
    leave a half-written state. Retries the commit once after a rollback; if
    it still fails, raises DatabaseWriteError -- a clear, typed error the API
    layer can turn into a 503 -- rather than letting a raw SQLAlchemy
    exception (or worse, a partially-committed transaction) leak out.
    """
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            db.commit()
            return
        except SQLAlchemyError as exc:
            last_error = exc
            db.rollback()
    raise DatabaseWriteError(f"Database commit failed after {attempts} attempts: {last_error}")


def _get_or_create_customer(db: Session, event: NormalizedEvent) -> Customer:
    customer = db.query(Customer).filter(Customer.external_customer_id == event.customer_id).first()
    if customer is not None:
        return customer
    customer = Customer(
        external_customer_id=event.customer_id,
        opted_out=event.customer_history.opted_out,
    )
    db.add(customer)
    db.flush()
    return customer


def _get_or_create_payment(db: Session, customer: Customer, event: NormalizedEvent) -> tuple[Payment, bool]:
    """Returns (payment, created). Idempotent on the external payment id."""
    payment = db.query(Payment).filter(Payment.razorpay_payment_id == event.payment_id).first()
    if payment is not None:
        return payment, False

    payment = Payment(
        customer_id=customer.id,
        razorpay_payment_id=event.payment_id,
        amount=event.amount,
        currency=event.currency,
        payment_method=ORMPaymentMethod(event.payment_method),
        status=ORMPaymentStatus(event.status),
        source=EventSource.SIMULATOR,
        dataset_split=DatasetSplit.HOLDOUT,
    )
    db.add(payment)
    db.flush()
    return payment, True


def _record_attempt(db: Session, payment: Payment, event: NormalizedEvent) -> None:
    """
    Always appends a new PaymentAttempt for a (non-duplicate) event, whether
    or not the Payment row itself was just created. A Payment can legitimately
    receive multiple events over time (e.g. successive webhook deliveries for
    the same razorpay_payment_id as retries happen) and every one of them is a
    real attempt that must show up in the payment's history.
    """
    db.add(
        PaymentAttempt(
            payment_id=payment.id,
            attempt_number=event.attempt_number,
            status="success" if event.status in {"recovered", "created"} else "failed",
            failure_code=event.failure_code,
            failure_reason=event.failure_reason,
            timestamp=event.timestamp,
            raw_event_json=event.model_dump(mode="json"),
        )
    )
    db.flush()


def _get_or_create_open_case(db: Session, payment: Payment, risk_score: float, revenue_at_risk: int) -> RecoveryCase:
    case = (
        db.query(RecoveryCase)
        .filter(RecoveryCase.payment_id == payment.id, RecoveryCase.status == RecoveryCaseStatus.OPEN)
        .first()
    )
    if case is not None:
        case.risk_score = risk_score
        case.revenue_at_risk = revenue_at_risk
        return case
    case = RecoveryCase(payment_id=payment.id, risk_score=risk_score, revenue_at_risk=revenue_at_risk)
    db.add(case)
    db.flush()
    return case


def run_pipeline_for_event(
    db: Session,
    event: NormalizedEvent,
    *,
    diagnosis_engine: DiagnosisEngine,
    recovery_agent: RecoveryAgent,
    gateway: PaymentGateway,
) -> dict[str, Any]:
    """
    Runs the full recovery pipeline for one normalized event, persisting every
    intermediate result and writing the complete audit chain. Commits at the
    end on success; callers running a batch should still wrap this in their
    own error handling per-event (Phase 13 covers failure injection more
    thoroughly) so one bad record doesn't abort an entire batch run.
    """
    settings = get_settings()
    audit = AuditLogger(db)

    customer = _get_or_create_customer(db, event)
    payment, _created = _get_or_create_payment(db, customer, event)

    # Duplicate detection: spec §4 -- "Duplicate event -> ignore." Checked
    # after we have a payment row to attach the log to, but before any further
    # processing happens.
    if audit.event_already_seen(event.event_id):
        audit.log(
            event_id=event.event_id,
            payment_id=payment.id,
            stage=AuditStage.DUPLICATE_IGNORED.value,
            payload={"reason": "duplicate event_id, no further processing"},
        )
        _commit_with_retry(db)
        return {"ignored": True, "payment_id": payment.id}

    _record_attempt(db, payment, event)

    audit.log(
        event_id=event.event_id,
        payment_id=payment.id,
        stage=AuditStage.PAYMENT_FAILED.value,
        payload={"status": event.status, "failure_code": event.failure_code, "attempt_number": event.attempt_number},
    )

    risk = assess_risk(event)
    audit.log(
        event_id=event.event_id,
        payment_id=payment.id,
        stage=AuditStage.RISK_DETECTED.value,
        payload=risk.model_dump(),
    )

    payment_already_recovered = payment.status == ORMPaymentStatus.RECOVERED

    case = _get_or_create_open_case(db, payment, risk.risk_score, risk.revenue_at_risk)

    context = compute_customer_context(event.customer_history, recovery_attempts=case.recovery_attempts)
    customer.segment = CustomerSegment(context.customer_segment)

    diagnosis = diagnosis_engine.diagnose(event, context, risk)
    db.add(
        AIAnalysis(
            recovery_case_id=case.id,
            stage=AIStage.DIAGNOSIS,
            model_provider=diagnosis.model_provider,
            raw_output_json=diagnosis.model_dump(),
            diagnosis=diagnosis.diagnosis,
            confidence=diagnosis.confidence,
            reasoning_summary=diagnosis.reasoning_summary,
            schema_valid=diagnosis.schema_valid,
        )
    )
    audit.log(
        event_id=event.event_id,
        payment_id=payment.id,
        stage=AuditStage.AI_DIAGNOSED.value,
        payload=diagnosis.model_dump(),
    )
    if diagnosis.fallback_used:
        audit.log(
            event_id=event.event_id,
            payment_id=payment.id,
            stage=AuditStage.AI_FALLBACK_USED.value,
            payload={"attempted_provider": diagnosis.attempted_provider, "stage": "diagnosis"},
        )

    proposal = recovery_agent.propose(event, context, diagnosis, risk)
    db.add(
        AIAnalysis(
            recovery_case_id=case.id,
            stage=AIStage.ACTION_PROPOSAL,
            model_provider=proposal.model_provider,
            raw_output_json=proposal.model_dump(),
            reasoning_summary=proposal.reason,
            confidence=proposal.confidence,
            schema_valid=proposal.schema_valid,
        )
    )
    audit.log(
        event_id=event.event_id,
        payment_id=payment.id,
        stage=AuditStage.ACTION_PROPOSED.value,
        payload=proposal.model_dump(),
    )
    if proposal.fallback_used and not diagnosis.fallback_used:
        # Only log a second AI_FALLBACK_USED if the diagnosis stage didn't
        # already flag it -- avoids a duplicate notice for the common case
        # where provider=None caused both stages to fall back together.
        audit.log(
            event_id=event.event_id,
            payment_id=payment.id,
            stage=AuditStage.AI_FALLBACK_USED.value,
            payload={"attempted_provider": proposal.attempted_provider, "stage": "action_proposal"},
        )

    decision = evaluate_policy(
        proposal, event, context, diagnosis, risk,
        payment_already_recovered=payment_already_recovered,
        is_duplicate_event=False,  # already handled above
    )

    action_row = RecoveryAction(
        recovery_case_id=case.id,
        proposed_action=ORMRecoveryActionType(proposal.action),
        policy_allowed=decision.allowed,
        policy_reason=decision.reason,
    )
    db.add(action_row)
    db.flush()

    audit.log(
        event_id=event.event_id,
        payment_id=payment.id,
        stage=(AuditStage.POLICY_APPROVED if decision.allowed else AuditStage.POLICY_BLOCKED).value,
        payload=decision.model_dump(),
    )

    exec_result = None

    if decision.final_action == "IGNORE":
        _commit_with_retry(db)
        return {"payment_id": payment.id, "case_status": case.status.value, "final_action": "IGNORE"}

    if decision.final_action == "ESCALATE_HUMAN":
        case.status = RecoveryCaseStatus.ESCALATED
        audit.log(
            event_id=event.event_id,
            payment_id=payment.id,
            stage=AuditStage.ESCALATED.value,
            payload={"reason": decision.reason},
        )
    elif decision.final_action == "STOP":
        case.status = RecoveryCaseStatus.STOPPED
        case.closed_at = _now()
        action_row.executed = False
        action_row.execution_result = "skipped"
    else:
        exec_result = gateway.execute(decision.final_action, event, diagnosis, risk.revenue_at_risk)
        action_row.executed = exec_result.executed
        action_row.execution_result = exec_result.result
        action_row.revenue_recovered = exec_result.revenue_recovered
        audit.log(
            event_id=event.event_id,
            payment_id=payment.id,
            stage=AuditStage.ACTION_EXECUTED.value,
            payload=exec_result.model_dump(),
        )

        case.recovery_attempts += 1

        if exec_result.result == "success":
            payment.status = ORMPaymentStatus.RECOVERED
            case.status = RecoveryCaseStatus.RECOVERED
            case.closed_at = _now()
            audit.log(
                event_id=event.event_id,
                payment_id=payment.id,
                stage=AuditStage.PAYMENT_RECOVERED.value,
                payload={"revenue_recovered": exec_result.revenue_recovered},
            )
        elif exec_result.result == "failed" and case.recovery_attempts >= settings.max_recovery_attempts:
            payment.status = ORMPaymentStatus.UNRECOVERABLE
            case.status = RecoveryCaseStatus.CLOSED_UNRECOVERED
            case.closed_at = _now()

    _commit_with_retry(db)

    return {
        "payment_id": payment.id,
        "case_status": case.status.value,
        "final_action": decision.final_action,
        "revenue_recovered": exec_result.revenue_recovered if exec_result else 0,
    }
