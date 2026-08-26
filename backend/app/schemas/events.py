"""
The normalized event schema. Every ingestion source (Razorpay Test Mode webhook,
CSV upload, or the synthetic simulator from Phase 3) is converted into this shape
before touching any service. This is deliberately the same field set the
synthetic generator emits as its "AI-facing" projection (see
scripts/generate_data.py::test_ground_truth_is_absent_from_ai_facing_projection) --
ground_truth/label fields never appear here, only in the offline evaluation path.
"""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

PaymentStatus = Literal["created", "failed", "recovered", "unrecoverable", "pending"]
PaymentMethod = Literal["card", "upi", "netbanking", "wallet", "emi"]


class CustomerHistory(BaseModel):
    previous_successful_payments: int = 0
    previous_failed_payments: int = 0
    lifetime_value: int = 0  # minor units
    last_successful_payment_at: datetime | None = None
    opted_out: bool = False


class NormalizedEvent(BaseModel):
    event_id: str
    customer_id: str
    payment_id: str
    amount: int = Field(gt=0, description="Minor units (paise)")
    currency: str = "INR"
    status: PaymentStatus
    failure_code: str | None = None
    failure_reason: str | None = None
    timestamp: datetime
    payment_method: PaymentMethod
    attempt_number: int = Field(ge=1)
    customer_history: CustomerHistory = Field(default_factory=CustomerHistory)
