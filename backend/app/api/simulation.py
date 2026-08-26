"""
Demo-mode entrypoint (spec §21): generate synthetic payment events and run
every one of them through the full pipeline (risk -> diagnosis -> agent ->
policy -> execution -> audit), populating the operational database the rest
of the dashboard reads from.

Distinct from scripts/run_evaluation.py: that script scores the pipeline
against ground truth on the *held-out* split of an offline dataset file. This
endpoint has no ground truth at all -- it's simulating what would happen with
live traffic, which is exactly why it doesn't reuse the evaluation engine.
"""
import sys
import time
from pathlib import Path

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.deps import get_diagnosis_engine, get_gateway, get_recovery_agent
from app.db.session import get_db
from app.models.recovery import RecoveryAction, RecoveryCase
from app.schemas.api_responses import SimulationGenerateResponse
from app.schemas.events import NormalizedEvent
from app.services.diagnosis.engine import DiagnosisEngine
from app.services.payment_gateway.gateway import PaymentGateway
from app.services.pipeline.orchestrator import run_pipeline_for_event
from app.services.recovery_agent.agent import RecoveryAgent

SCRIPTS_DIR = Path(__file__).resolve().parents[3] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from generate_data import generate_events  # noqa: E402

router = APIRouter(tags=["simulation"])

AI_FACING_KEYS = {
    "event_id", "customer_id", "payment_id", "amount", "currency", "status",
    "failure_code", "failure_reason", "timestamp", "payment_method",
    "attempt_number", "customer_history",
}


@router.post("/simulation/generate", response_model=SimulationGenerateResponse)
def generate_simulation(
    records: int = 300,
    seed: int | None = None,
    db: Session = Depends(get_db),
    diagnosis_engine: DiagnosisEngine = Depends(get_diagnosis_engine),
    recovery_agent: RecoveryAgent = Depends(get_recovery_agent),
    gateway: PaymentGateway = Depends(get_gateway),
) -> SimulationGenerateResponse:
    seed = seed if seed is not None else int(time.time())
    raw_events, _manifest = generate_events(records=records, seed=seed)

    processed = 0
    errors = 0
    recovered = 0
    escalated = 0
    revenue_recovered_total = 0

    for raw in raw_events:
        try:
            view = {k: v for k, v in raw.items() if k in AI_FACING_KEYS}
            event = NormalizedEvent(**view)
            result = run_pipeline_for_event(
                db, event,
                diagnosis_engine=diagnosis_engine,
                recovery_agent=recovery_agent,
                gateway=gateway,
            )
            processed += 1
            if result.get("case_status") == "recovered":
                recovered += 1
                revenue_recovered_total += result.get("revenue_recovered", 0)
            elif result.get("case_status") == "escalated":
                escalated += 1
        except Exception:
            db.rollback()
            errors += 1

    revenue_at_risk_total = db.query(func.coalesce(func.sum(RecoveryCase.revenue_at_risk), 0)).scalar()
    blocked = db.query(RecoveryAction).filter(RecoveryAction.policy_allowed.is_(False)).count()

    return SimulationGenerateResponse(
        records_requested=records,
        records_processed=processed,
        errors=errors,
        recovered=recovered,
        escalated=escalated,
        blocked_actions=blocked,
        revenue_at_risk=int(revenue_at_risk_total or 0),
        revenue_recovered=revenue_recovered_total,
        seed=seed,
    )
