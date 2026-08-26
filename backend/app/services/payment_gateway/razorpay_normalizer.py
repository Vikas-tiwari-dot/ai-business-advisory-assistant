"""
Converts a Razorpay Test Mode webhook payload into the fields our internal
NormalizedEvent schema needs (spec module A: Event Ingestion, webhook source).

Two fields Razorpay's webhook payload genuinely does not provide, and why:

  - `attempt_number`: Razorpay has no concept of "this is the Nth attempt at
    recovering this payment" -- that's our own business concept. The caller
    (app/api/events.py) fills this in by counting prior PaymentAttempt rows
    for the same razorpay_payment_id in our own database.
  - `customer_history`: same story -- this is derived from OUR prior payment
    history for the customer, not anything Razorpay sends. The caller looks
    up the Customer row (if one already exists) and computes it.

This module is intentionally pure and DB-free so it's unit-testable without
a database -- it returns a partial dict; the API layer enriches it.
"""
from datetime import datetime, timezone
from typing import Any

RAZORPAY_STATUS_MAP = {
    "captured": "recovered",
    "refunded": "recovered",  # captured then refunded -- money did reach us at some point
    "failed": "failed",
    "authorized": "pending",
    "created": "pending",
}

RAZORPAY_METHOD_MAP = {
    "card": "card",
    "upi": "upi",
    "netbanking": "netbanking",
    "wallet": "wallet",
    "emi": "emi",
    # Razorpay supports methods we don't model separately (paylater,
    # bank_transfer, etc.) -- fold them into the closest fit rather than
    # crashing on an unrecognized method. This is a real, documented
    # simplification, not silent data loss: see the "unmapped_method" note
    # attached to the returned dict when this fallback fires.
}
DEFAULT_METHOD_FALLBACK = "card"


class MalformedWebhookError(ValueError):
    """Raised when the payload doesn't have the shape a Razorpay payment webhook should have."""


def extract_payment_entity(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        return payload["payload"]["payment"]["entity"]
    except (KeyError, TypeError) as exc:
        raise MalformedWebhookError(f"Missing payload.payment.entity: {exc}") from exc


def build_partial_event_from_webhook(payload: dict[str, Any]) -> dict[str, Any]:
    """
    Returns a dict with every NormalizedEvent field EXCEPT attempt_number and
    customer_history, which the caller must fill in from the database. Also
    includes `razorpay_event_type` and `unmapped_method` as extra debugging
    context, which callers should strip before constructing a NormalizedEvent.
    """
    entity = extract_payment_entity(payload)

    required = ("id", "amount", "currency", "status")
    missing = [f for f in required if f not in entity]
    if missing:
        raise MalformedWebhookError(f"payment entity missing required fields: {missing}")

    razorpay_event_type = payload.get("event", "unknown")
    created_at = entity.get("created_at")
    if created_at is not None:
        timestamp = datetime.fromtimestamp(created_at, tz=timezone.utc).isoformat()
    else:
        timestamp = datetime.now(timezone.utc).isoformat()

    internal_status = RAZORPAY_STATUS_MAP.get(entity["status"], "pending")

    raw_method = entity.get("method")
    unmapped_method = raw_method is not None and raw_method not in RAZORPAY_METHOD_MAP
    payment_method = RAZORPAY_METHOD_MAP.get(raw_method, DEFAULT_METHOD_FALLBACK)

    # Razorpay's payment entity doesn't carry our own customer_id -- fall back
    # through whatever identifying fields are present. A real integration
    # would look up/create the customer via the Razorpay Customer API and
    # store the mapping; this is the honest minimum for a buildathon scope.
    customer_id = entity.get("customer_id") or entity.get("email") or entity.get("contact") or f"unknown_{entity['id']}"

    event_id = f"rzp_{razorpay_event_type}_{entity['id']}_{entity.get('created_at', 'na')}"

    return {
        "event_id": event_id,
        "customer_id": customer_id,
        "payment_id": entity["id"],
        "amount": entity["amount"],
        "currency": entity.get("currency", "INR"),
        "status": internal_status,
        "failure_code": entity.get("error_code"),
        "failure_reason": entity.get("error_description"),
        "timestamp": timestamp,
        "payment_method": payment_method,
        "razorpay_event_type": razorpay_event_type,
        "unmapped_method": unmapped_method,
    }
