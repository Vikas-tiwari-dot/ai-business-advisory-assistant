from collections import defaultdict

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.enums import RecoveryCaseStatus
from app.models.payment import Payment
from app.models.recovery import AIAnalysis, RecoveryAction, RecoveryCase
from app.schemas.api_responses import MetricsResponse, _inr

router = APIRouter(tags=["metrics"])


@router.get("/metrics", response_model=MetricsResponse)
def get_metrics(db: Session = Depends(get_db)) -> MetricsResponse:
    revenue_at_risk = int(
        db.query(func.coalesce(func.sum(RecoveryCase.revenue_at_risk), 0))
        .filter(RecoveryCase.status.in_([RecoveryCaseStatus.OPEN, RecoveryCaseStatus.ESCALATED]))
        .scalar()
        or 0
    )
    revenue_recovered = int(
        db.query(func.coalesce(func.sum(RecoveryAction.revenue_recovered), 0))
        .filter(RecoveryAction.execution_result == "success")
        .scalar()
        or 0
    )
    total_ever_at_risk = int(db.query(func.coalesce(func.sum(RecoveryCase.revenue_at_risk), 0)).scalar() or 0)
    recovery_rate = (revenue_recovered / total_ever_at_risk) if total_ever_at_risk > 0 else 0.0

    payments_recovered = db.query(Payment).filter(Payment.status == "recovered").count()
    pending_recovery = db.query(RecoveryCase).filter(RecoveryCase.status == RecoveryCaseStatus.OPEN).count()
    human_escalations = db.query(RecoveryCase).filter(RecoveryCase.status == RecoveryCaseStatus.ESCALATED).count()
    blocked_actions = db.query(RecoveryAction).filter(RecoveryAction.policy_allowed.is_(False)).count()

    failure_categories = [
        {"category": category or "unknown", "count": count}
        for category, count in (
            db.query(AIAnalysis.diagnosis, func.count(AIAnalysis.id))
            .filter(AIAnalysis.stage == "diagnosis")
            .group_by(AIAnalysis.diagnosis)
            .all()
        )
    ]

    recovery_actions_breakdown = [
        {"action": action.value, "count": count}
        for action, count in (
            db.query(RecoveryAction.proposed_action, func.count(RecoveryAction.id))
            .group_by(RecoveryAction.proposed_action)
            .all()
        )
    ]

    recovery_outcomes = [
        {"outcome": outcome or "pending", "count": count}
        for outcome, count in (
            db.query(RecoveryAction.execution_result, func.count(RecoveryAction.id))
            .group_by(RecoveryAction.execution_result)
            .all()
        )
    ]

    revenue_by_day: dict[str, dict[str, int]] = defaultdict(lambda: {"at_risk": 0, "recovered": 0})
    for case in db.query(RecoveryCase.opened_at, RecoveryCase.revenue_at_risk).all():
        day = case.opened_at.strftime("%Y-%m-%d")
        revenue_by_day[day]["at_risk"] += case.revenue_at_risk
    for action in db.query(RecoveryAction.created_at, RecoveryAction.revenue_recovered).filter(
        RecoveryAction.execution_result == "success"
    ).all():
        day = action.created_at.strftime("%Y-%m-%d")
        revenue_by_day[day]["recovered"] += action.revenue_recovered

    revenue_over_time = [
        {"date": day, "at_risk": vals["at_risk"], "recovered": vals["recovered"]}
        for day, vals in sorted(revenue_by_day.items())
    ]

    return MetricsResponse(
        revenue_at_risk=revenue_at_risk,
        revenue_at_risk_display=_inr(revenue_at_risk),
        revenue_recovered=revenue_recovered,
        revenue_recovered_display=_inr(revenue_recovered),
        recovery_rate=round(recovery_rate, 4),
        payments_recovered=payments_recovered,
        pending_recovery=pending_recovery,
        human_escalations=human_escalations,
        blocked_actions=blocked_actions,
        failure_categories=failure_categories,
        recovery_actions_breakdown=recovery_actions_breakdown,
        recovery_outcomes=recovery_outcomes,
        revenue_over_time=revenue_over_time,
    )
