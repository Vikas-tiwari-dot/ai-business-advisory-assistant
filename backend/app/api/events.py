"""
Event Ingestion (spec module A). Two sources land here:

  - POST /api/events/webhook: Razorpay Test Mode webhooks, signature-verified.
  - POST /api/events/payment: already-normalized events submitted directly
    (what the CSV-upload and synthetic-simulator paths would also feed
    through, if wired up -- both ultimately produce the same NormalizedEvent
    shape this endpoint accepts).

Both funnel into the same run_pipeline_for_event() orchestrator used
everywhere else, so ingestion source has zero effect on how an event is
processed -- exactly the "normalize every event into one internal schema"
requirement from the spec.
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.exc import OperationalError

from app.api.deps import get_diagnosis_engine, get_gateway, get_recovery_agent
from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.models.customer import Customer
from app.models.payment import Payment, PaymentAttempt
from app.schemas.events import CustomerHistory, NormalizedEvent
from app.services.diagnosis.engine import DiagnosisEngine
from app.services.payment_gateway.gateway import PaymentGateway
from app.services.payment_gateway.razorpay_normalizer import (
    MalformedWebhookError,
    build_partial_event_from_webhook,
)
from app.services.payment_gateway.webhook_security import verify_signature
from app.services.pipeline.orchestrator import run_pipeline_for_event
from app.services.recovery_agent.agent import RecoveryAgent

from sqlalchemy.orm import Session

router = APIRouter(tags=["events"])


def _enrich_with_db_history(db: Session, partial: dict) -> dict:
    """
    Fills in attempt_number and customer_history from our own database, since
    Razorpay's webhook payload has neither -- see razorpay_normalizer.py's
    module docstring for why.
    """
    prior_attempts = (
        db.query(PaymentAttempt)
        .join(Payment, Payment.id == PaymentAttempt.payment_id)
        .filter(Payment.razorpay_payment_id == partial["payment_id"])
        .count()
    )
    attempt_number = prior_attempts + 1

    customer = db.query(Customer).filter(Customer.external_customer_id == partial["customer_id"]).first()
    if customer is None:
        history = CustomerHistory()
    else:
        payments = db.query(Payment).filter(Payment.customer_id == customer.id).all()
        successful = sum(1 for p in payments if p.status.value == "recovered")
        failed = sum(1 for p in payments if p.status.value == "failed")
        lifetime_value = sum(p.amount for p in payments if p.status.value == "recovered")
        history = CustomerHistory(
            previous_successful_payments=successful,
            previous_failed_payments=failed,
            lifetime_value=lifetime_value,
            opted_out=customer.opted_out,
        )

    return {
        **{k: v for k, v in partial.items() if k not in ("razorpay_event_type", "unmapped_method")},
        "attempt_number": attempt_number,
        "customer_history": history,
    }


@router.post("/events/webhook")
async def receive_webhook(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    diagnosis_engine: DiagnosisEngine = Depends(get_diagnosis_engine),
    recovery_agent: RecoveryAgent = Depends(get_recovery_agent),
    gateway: PaymentGateway = Depends(get_gateway),
) -> dict:
    if not settings.razorpay_webhook_secret:
        raise HTTPException(
            status_code=503,
            detail="RAZORPAY_WEBHOOK_SECRET is not configured; refusing to accept unverifiable webhooks.",
        )

    raw_body = await request.body()
    signature = request.headers.get("X-Razorpay-Signature")

    if not verify_signature(raw_body, signature, settings.razorpay_webhook_secret):
        raise HTTPException(status_code=401, detail="Invalid webhook signature.")

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=422, detail="Malformed webhook payload: not valid JSON.")

    try:
        partial = build_partial_event_from_webhook(payload)
    except MalformedWebhookError as exc:
        raise HTTPException(status_code=422, detail=f"Malformed webhook payload: {exc}")

    enriched = _enrich_with_db_history(db, partial)

    try:
        event = NormalizedEvent(**enriched)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Webhook payload failed normalization: {exc}")

    result = run_pipeline_for_event(
        db, event, diagnosis_engine=diagnosis_engine, recovery_agent=recovery_agent, gateway=gateway
    )
    return {"received": True, **result}


@router.post("/events/payment")
def receive_payment_event(
    event: NormalizedEvent,
    db: Session = Depends(get_db),
    diagnosis_engine: DiagnosisEngine = Depends(get_diagnosis_engine),
    recovery_agent: RecoveryAgent = Depends(get_recovery_agent),
    gateway: PaymentGateway = Depends(get_gateway),
) -> dict:
    result = run_pipeline_for_event(
        db, event, diagnosis_engine=diagnosis_engine, recovery_agent=recovery_agent, gateway=gateway
    )
    return {"received": True, **result}
