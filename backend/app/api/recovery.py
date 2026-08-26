import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_gateway
from app.db.session import get_db
from app.models.enums import AuditStage, RecoveryCaseStatus
from app.models.payment import Payment, PaymentAttempt
from app.models.recovery import AIAnalysis, RecoveryAction, RecoveryCase
from app.schemas.api_responses import QueueItem, _inr
from app.schemas.events import NormalizedEvent
from app.services.audit.logger import AuditLogger
from app.services.payment_gateway.gateway import PaymentGateway

router = APIRouter(tags=["recovery"])


def _now() -> datetime:
    return datetime.now(timezone.utc)


@router.get("/recovery/queue", response_model=list[QueueItem])
def get_queue(db: Session = Depends(get_db)) -> list[QueueItem]:
    cases = (
        db.query(RecoveryCase)
        .filter(RecoveryCase.status == RecoveryCaseStatus.ESCALATED)
        .order_by(RecoveryCase.revenue_at_risk.desc())
        .all()
    )

    items = []
    for case in cases:
        payment = db.get(Payment, case.payment_id)
        latest_analysis = (
            db.query(AIAnalysis)
            .filter(AIAnalysis.recovery_case_id == case.id, AIAnalysis.stage == "diagnosis")
            .order_by(AIAnalysis.created_at.desc())
            .first()
        )
        latest_action = (
            db.query(RecoveryAction)
            .filter(RecoveryAction.recovery_case_id == case.id)
            .order_by(RecoveryAction.created_at.desc())
            .first()
        )
        items.append(
            QueueItem(
                payment_id=str(payment.id),
                customer_external_id=payment.customer.external_customer_id,
                amount_display=_inr(payment.amount),
                risk_score=case.risk_score,
                revenue_at_risk=case.revenue_at_risk,
                revenue_at_risk_display=_inr(case.revenue_at_risk),
                case_status=case.status.value,
                diagnosis=latest_analysis.diagnosis if latest_analysis else None,
                diagnosis_confidence=latest_analysis.confidence if latest_analysis else None,
                proposed_action=latest_action.proposed_action.value if latest_action else None,
                policy_reason=latest_action.policy_reason if latest_action else None,
                opened_at=case.opened_at,
            )
        )
    return items


def _get_open_or_escalated_case(db: Session, payment_id: str) -> RecoveryCase:
    try:
        pid = uuid.UUID(payment_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid payment_id format")

    case = (
        db.query(RecoveryCase)
        .filter(RecoveryCase.payment_id == pid, RecoveryCase.status == RecoveryCaseStatus.ESCALATED)
        .order_by(RecoveryCase.opened_at.desc())
        .first()
    )
    if case is None:
        raise HTTPException(status_code=404, detail="No escalated recovery case found for this payment")
    return case


def _reconstruct_event(db: Session, payment: Payment) -> NormalizedEvent:
    """The orchestrator stores the full original normalized event on each PaymentAttempt."""
    latest_attempt = (
        db.query(PaymentAttempt)
        .filter(PaymentAttempt.payment_id == payment.id)
        .order_by(PaymentAttempt.attempt_number.desc())
        .first()
    )
    if latest_attempt is None or not latest_attempt.raw_event_json:
        raise HTTPException(status_code=500, detail="Original event data not available for this payment")
    return NormalizedEvent(**latest_attempt.raw_event_json)


@router.post("/recovery/{payment_id}/approve")
def approve_recovery(
    payment_id: str,
    db: Session = Depends(get_db),
    gateway: PaymentGateway = Depends(get_gateway),
) -> dict:
    case = _get_open_or_escalated_case(db, payment_id)
    payment = db.get(Payment, case.payment_id)
    action_row = (
        db.query(RecoveryAction)
        .filter(RecoveryAction.recovery_case_id == case.id)
        .order_by(RecoveryAction.created_at.desc())
        .first()
    )
    if action_row is None:
        raise HTTPException(status_code=500, detail="No proposed action on record for this case")

    audit = AuditLogger(db)
    audit.log(
        event_id=f"human-approve-{case.id}",
        payment_id=payment.id,
        stage=AuditStage.HUMAN_DECISION.value,
        payload={"decision": "approve", "action": action_row.proposed_action.value},
    )

    latest_analysis = (
        db.query(AIAnalysis)
        .filter(AIAnalysis.recovery_case_id == case.id, AIAnalysis.stage == "diagnosis")
        .order_by(AIAnalysis.created_at.desc())
        .first()
    )
    event = _reconstruct_event(db, payment)

    from app.schemas.diagnosis import DiagnosisResult

    diagnosis = DiagnosisResult(
        diagnosis=latest_analysis.diagnosis if latest_analysis else "unknown",
        confidence=latest_analysis.confidence if latest_analysis else 0.5,
        reasoning_summary=latest_analysis.reasoning_summary if latest_analysis else "Human-approved recovery.",
        recommended_strategy="human_approved",
        model_provider=latest_analysis.model_provider if latest_analysis else "fallback_rules",
        schema_valid=True,
        fallback_used=True,
        attempted_provider=None,
    )

    exec_result = gateway.execute(action_row.proposed_action.value, event, diagnosis, case.revenue_at_risk)
    action_row.human_override = "approve"
    action_row.executed = exec_result.executed
    action_row.execution_result = exec_result.result
    action_row.revenue_recovered = exec_result.revenue_recovered

    audit.log(
        event_id=f"human-approve-{case.id}",
        payment_id=payment.id,
        stage=AuditStage.ACTION_EXECUTED.value,
        payload=exec_result.model_dump(),
    )

    if exec_result.result == "success":
        payment.status = "recovered"
        case.status = RecoveryCaseStatus.RECOVERED
        case.closed_at = _now()
        audit.log(
            event_id=f"human-approve-{case.id}",
            payment_id=payment.id,
            stage=AuditStage.PAYMENT_RECOVERED.value,
            payload={"revenue_recovered": exec_result.revenue_recovered},
        )
    else:
        case.status = RecoveryCaseStatus.OPEN

    db.commit()
    return {"payment_id": str(payment.id), "case_status": case.status.value, "execution_result": exec_result.result}


@router.post("/recovery/{payment_id}/reject")
def reject_recovery(payment_id: str, db: Session = Depends(get_db)) -> dict:
    case = _get_open_or_escalated_case(db, payment_id)
    payment = db.get(Payment, case.payment_id)

    action_row = (
        db.query(RecoveryAction)
        .filter(RecoveryAction.recovery_case_id == case.id)
        .order_by(RecoveryAction.created_at.desc())
        .first()
    )
    if action_row:
        action_row.human_override = "reject"
        action_row.executed = False
        action_row.execution_result = "skipped"

    case.status = RecoveryCaseStatus.CLOSED_UNRECOVERED
    case.closed_at = _now()

    AuditLogger(db).log(
        event_id=f"human-reject-{case.id}",
        payment_id=payment.id,
        stage=AuditStage.HUMAN_DECISION.value,
        payload={"decision": "reject"},
    )
    db.commit()
    return {"payment_id": str(payment.id), "case_status": case.status.value}


@router.post("/recovery/{payment_id}/escalate")
def escalate_further(payment_id: str, db: Session = Depends(get_db)) -> dict:
    case = _get_open_or_escalated_case(db, payment_id)
    payment = db.get(Payment, case.payment_id)

    AuditLogger(db).log(
        event_id=f"human-escalate-{case.id}",
        payment_id=payment.id,
        stage=AuditStage.HUMAN_DECISION.value,
        payload={"decision": "escalate_further"},
    )
    db.commit()
    return {"payment_id": str(payment.id), "case_status": case.status.value}


@router.post("/recovery/{payment_id}/stop")
def stop_recovery(payment_id: str, db: Session = Depends(get_db)) -> dict:
    case = _get_open_or_escalated_case(db, payment_id)
    payment = db.get(Payment, case.payment_id)

    action_row = (
        db.query(RecoveryAction)
        .filter(RecoveryAction.recovery_case_id == case.id)
        .order_by(RecoveryAction.created_at.desc())
        .first()
    )
    if action_row:
        action_row.human_override = "stop"
        action_row.executed = False
        action_row.execution_result = "skipped"

    case.status = RecoveryCaseStatus.STOPPED
    case.closed_at = _now()

    AuditLogger(db).log(
        event_id=f"human-stop-{case.id}",
        payment_id=payment.id,
        stage=AuditStage.HUMAN_DECISION.value,
        payload={"decision": "stop"},
    )
    db.commit()
    return {"payment_id": str(payment.id), "case_status": case.status.value}
