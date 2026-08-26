import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.customer import Customer
from app.models.enums import RecoveryCaseStatus
from app.models.payment import Payment, PaymentAttempt
from app.models.recovery import AIAnalysis, RecoveryAction, RecoveryCase
from app.schemas.api_responses import (
    AIAnalysisSummary,
    AttemptSummary,
    AuditEntry,
    PaymentDetail,
    PaymentListResponse,
    PaymentSummary,
    RecoveryActionSummary,
    _inr,
)
from app.services.audit.logger import AuditLogger

router = APIRouter(tags=["payments"])


def _latest_case(db: Session, payment_id: uuid.UUID) -> RecoveryCase | None:
    return (
        db.query(RecoveryCase)
        .filter(RecoveryCase.payment_id == payment_id)
        .order_by(RecoveryCase.opened_at.desc())
        .first()
    )


def _to_summary(db: Session, payment: Payment) -> PaymentSummary:
    case = _latest_case(db, payment.id)
    return PaymentSummary(
        id=str(payment.id),
        customer_external_id=payment.customer.external_customer_id,
        razorpay_payment_id=payment.razorpay_payment_id,
        amount=payment.amount,
        amount_display=_inr(payment.amount),
        currency=payment.currency,
        status=payment.status.value,
        payment_method=payment.payment_method.value,
        risk_score=case.risk_score if case else None,
        revenue_at_risk=case.revenue_at_risk if case else None,
        case_status=case.status.value if case else None,
        created_at=payment.created_at,
    )


@router.get("/payments", response_model=PaymentListResponse)
def list_payments(
    status: str | None = Query(default=None),
    case_status: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    db: Session = Depends(get_db),
) -> PaymentListResponse:
    query = db.query(Payment).join(Customer)

    if status:
        query = query.filter(Payment.status == status)

    if case_status:
        query = (
            query.join(RecoveryCase, RecoveryCase.payment_id == Payment.id)
            .filter(RecoveryCase.status == case_status)
        )

    total = query.count()
    rows = (
        query.order_by(Payment.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    return PaymentListResponse(
        items=[_to_summary(db, p) for p in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/payments/{payment_id}", response_model=PaymentDetail)
def get_payment(payment_id: str, db: Session = Depends(get_db)) -> PaymentDetail:
    try:
        pid = uuid.UUID(payment_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid payment_id format")

    payment = db.get(Payment, pid)
    if payment is None:
        raise HTTPException(status_code=404, detail="Payment not found")

    attempts = (
        db.query(PaymentAttempt)
        .filter(PaymentAttempt.payment_id == pid)
        .order_by(PaymentAttempt.attempt_number.asc())
        .all()
    )

    case_ids = [c.id for c in db.query(RecoveryCase).filter(RecoveryCase.payment_id == pid).all()]

    analyses = (
        db.query(AIAnalysis).filter(AIAnalysis.recovery_case_id.in_(case_ids)).order_by(AIAnalysis.created_at.asc()).all()
        if case_ids else []
    )
    actions = (
        db.query(RecoveryAction).filter(RecoveryAction.recovery_case_id.in_(case_ids)).order_by(RecoveryAction.created_at.asc()).all()
        if case_ids else []
    )

    audit = AuditLogger(db)
    trail = audit.get_trail(pid)

    return PaymentDetail(
        payment=_to_summary(db, payment),
        attempts=[
            AttemptSummary(
                attempt_number=a.attempt_number,
                status=a.status,
                failure_code=a.failure_code,
                failure_reason=a.failure_reason,
                timestamp=a.timestamp,
            )
            for a in attempts
        ],
        analyses=[
            AIAnalysisSummary(
                stage=a.stage.value,
                model_provider=a.model_provider,
                diagnosis=a.diagnosis,
                confidence=a.confidence,
                reasoning_summary=a.reasoning_summary,
                schema_valid=a.schema_valid,
                created_at=a.created_at,
            )
            for a in analyses
        ],
        actions=[
            RecoveryActionSummary(
                proposed_action=a.proposed_action.value,
                policy_allowed=a.policy_allowed,
                policy_reason=a.policy_reason,
                executed=a.executed,
                execution_result=a.execution_result,
                revenue_recovered=a.revenue_recovered,
                revenue_recovered_display=_inr(a.revenue_recovered),
                human_override=a.human_override,
                created_at=a.created_at,
            )
            for a in actions
        ],
        audit_trail=[
            AuditEntry(
                id=str(e.id),
                event_id=e.event_id,
                stage=e.stage,
                payload=e.payload_json,
                system_version=e.system_version,
                timestamp=e.timestamp,
            )
            for e in trail
        ],
    )
