"""
Response schemas for the /api/* dashboard endpoints. Kept separate from the
domain schemas (app/schemas/*) used by the pipeline services -- these are
shaped for what the frontend needs to render, including the `_display`
currency strings the file_handling convention in this codebase uses for
money fields.
"""
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


def _inr(minor_units: int) -> str:
    return f"₹{minor_units / 100:,.0f}"


class PaymentSummary(BaseModel):
    id: str
    customer_external_id: str
    razorpay_payment_id: str | None
    amount: int
    amount_display: str
    currency: str
    status: str
    payment_method: str
    risk_score: float | None
    revenue_at_risk: int | None
    case_status: str | None
    created_at: datetime


class AttemptSummary(BaseModel):
    attempt_number: int
    status: str
    failure_code: str | None
    failure_reason: str | None
    timestamp: datetime


class AIAnalysisSummary(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    stage: str
    model_provider: str
    diagnosis: str | None
    confidence: float | None
    reasoning_summary: str
    schema_valid: bool
    created_at: datetime


class RecoveryActionSummary(BaseModel):
    proposed_action: str
    policy_allowed: bool
    policy_reason: str
    executed: bool
    execution_result: str | None
    revenue_recovered: int
    revenue_recovered_display: str
    human_override: str | None
    created_at: datetime


class AuditEntry(BaseModel):
    id: str
    event_id: str
    stage: str
    payload: dict[str, Any]
    system_version: str
    timestamp: datetime


class PaymentDetail(BaseModel):
    model_config = ConfigDict()

    payment: PaymentSummary
    attempts: list[AttemptSummary]
    analyses: list[AIAnalysisSummary]
    actions: list[RecoveryActionSummary]
    audit_trail: list[AuditEntry]


class PaymentListResponse(BaseModel):
    items: list[PaymentSummary]
    total: int
    page: int
    page_size: int


class QueueItem(BaseModel):
    payment_id: str
    customer_external_id: str
    amount_display: str
    risk_score: float
    revenue_at_risk: int
    revenue_at_risk_display: str
    case_status: str
    diagnosis: str | None
    diagnosis_confidence: float | None
    proposed_action: str | None
    policy_reason: str | None
    opened_at: datetime


class MetricsResponse(BaseModel):
    revenue_at_risk: int
    revenue_at_risk_display: str
    revenue_recovered: int
    revenue_recovered_display: str
    recovery_rate: float
    payments_recovered: int
    pending_recovery: int
    human_escalations: int
    blocked_actions: int
    failure_categories: list[dict[str, Any]]
    recovery_actions_breakdown: list[dict[str, Any]]
    recovery_outcomes: list[dict[str, Any]]
    revenue_over_time: list[dict[str, Any]]


class SimulationGenerateResponse(BaseModel):
    records_requested: int
    records_processed: int
    errors: int
    recovered: int
    escalated: int
    blocked_actions: int
    revenue_at_risk: int
    revenue_recovered: int
    seed: int


class EvaluationResponse(BaseModel):
    run_id: str | None
    dataset_split: str | None
    metrics: dict[str, Any] | None
    created_at: datetime | None
    available: bool
