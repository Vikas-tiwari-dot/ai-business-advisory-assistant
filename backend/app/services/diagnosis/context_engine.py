"""
Customer Context Engine (spec module D). Pure arithmetic on data already present
in the normalized event -- explicitly NOT an LLM call, per spec: "Do NOT use an
LLM for simple arithmetic or deterministic validation."

Segment thresholds mirror the ones used to *generate* the synthetic customer
pool in scripts/generate_data.py, but this function only ever looks at fields a
real system would actually have (payment/success counts, lifetime value) -- it
never sees the generator's ground_truth labels.
"""
from app.core.config import get_settings
from app.schemas.context import CustomerContext
from app.schemas.events import CustomerHistory


def compute_customer_context(
    history: CustomerHistory,
    recovery_attempts: int = 0,
) -> CustomerContext:
    total_payments = history.previous_successful_payments + history.previous_failed_payments
    failure_rate = (history.previous_failed_payments / total_payments) if total_payments else 0.0

    settings = get_settings()
    if (
        history.lifetime_value >= settings.high_value_escalation_threshold
        or history.previous_successful_payments >= 15
    ):
        segment: str = "high_value"
    elif history.previous_successful_payments >= 3:
        segment = "standard"
    else:
        segment = "new"

    return CustomerContext(
        customer_segment=segment,  # type: ignore[arg-type]
        lifetime_value=history.lifetime_value,
        failure_rate=round(failure_rate, 4),
        recovery_attempts=recovery_attempts,
    )
