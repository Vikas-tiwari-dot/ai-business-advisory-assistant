from typing import Literal

from pydantic import BaseModel, Field

RiskCategory = Literal["none", "payment_failure", "checkout_abandonment", "overdue_invoice"]


class RiskAssessment(BaseModel):
    risk_score: float = Field(ge=0.0, le=1.0)
    revenue_at_risk: int = Field(ge=0, description="Minor units (paise)")
    risk_category: RiskCategory
    confidence: float = Field(ge=0.0, le=1.0)
    # Not part of the spec's example output, but useful for the audit trail and
    # for debugging why a score came out the way it did -- purely deterministic,
    # no free text generation involved.
    signals: dict[str, float | int | str | bool] = Field(default_factory=dict)
