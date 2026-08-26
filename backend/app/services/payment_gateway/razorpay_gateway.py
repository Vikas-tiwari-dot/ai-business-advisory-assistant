"""
Razorpay Test Mode gateway adapter (spec §12).

IMPORTANT, read before relying on this in a demo: this has NOT been exercised
against a live Razorpay Test Mode endpoint. This sandbox's network egress is
restricted to package registries (pypi, npm, github) -- api.razorpay.com is
not reachable from here, so this code is written to the documented Razorpay
API shape but unverified end-to-end. Test manually with real Test Mode keys
(RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET) before depending on it.

Scope, and why it's narrower than the simulator: Razorpay's payments API does
not support "retrying" an already-failed payment in place -- a failed payment
is terminal. The realistic Test Mode integration for RETRY_PAYMENT /
SCHEDULE_RETRY / OFFER_ALTERNATE_METHOD is to create a fresh Payment Link
against the same order/customer and let the customer complete it. SEND_REMINDER
is a notification-service concern (email/SMS), not something the Razorpay
payments API does directly -- this adapter creates the Payment Link the
reminder would point to, but does not itself send an email/SMS.

Because a freshly created Payment Link is "pending customer action," not an
immediate success or failure, this adapter reports `result="skipped"` with
`executed=True` and the link URL in `message` -- it cannot honestly claim
"success" the way the simulator can, since nothing here waits for the
customer to actually pay. Treat this as "action initiated," not "revenue
recovered." A real production system would reconcile via a follow-up webhook,
which is out of scope for this buildathon's synchronous batch-eval pipeline.
"""
import httpx

from app.schemas.diagnosis import DiagnosisResult
from app.schemas.events import NormalizedEvent
from app.schemas.execution import ActionResult
from app.schemas.policy import ResolvedAction
from app.services.payment_gateway.gateway import NON_EXECUTING_ACTIONS, PaymentGateway

RAZORPAY_PAYMENT_LINKS_ENDPOINT = "https://api.razorpay.com/v1/payment_links"

# Actions this adapter can meaningfully act on by creating a Payment Link.
# SEND_REMINDER is included because in practice the "reminder" *is* a link to
# pay, sent via whatever channel -- the payments-side action is the same.
LINK_CREATING_ACTIONS: set[str] = {
    "RETRY_PAYMENT",
    "SCHEDULE_RETRY",
    "OFFER_ALTERNATE_METHOD",
    "SEND_REMINDER",
}


class RazorpayTestModeGateway(PaymentGateway):
    name = "razorpay_test_mode"

    def __init__(self, key_id: str, key_secret: str, timeout: float = 8.0):
        self.key_id = key_id
        self.key_secret = key_secret
        self.timeout = timeout

    def execute(
        self,
        action: ResolvedAction,
        event: NormalizedEvent,
        diagnosis: DiagnosisResult,
        revenue_at_risk: int,
    ) -> ActionResult:
        if action in NON_EXECUTING_ACTIONS:
            return ActionResult(
                executed=False,
                result="skipped",
                revenue_recovered=0,
                message=f"{action} does not execute against a payment gateway.",
                gateway=self.name,
            )

        if action not in LINK_CREATING_ACTIONS:
            # Defensive: should be unreachable given the closed ResolvedAction set,
            # but fail loudly rather than guessing if it ever is.
            return ActionResult(
                executed=False,
                result="skipped",
                revenue_recovered=0,
                message=f"{action} is not supported by the Razorpay Test Mode adapter.",
                gateway=self.name,
            )

        try:
            response = httpx.post(
                RAZORPAY_PAYMENT_LINKS_ENDPOINT,
                auth=(self.key_id, self.key_secret),
                json={
                    "amount": revenue_at_risk,
                    "currency": event.currency,
                    "description": f"Recovery attempt for payment {event.payment_id} ({diagnosis.diagnosis})",
                    "reference_id": event.payment_id,
                    "notes": {"recovery_action": action, "diagnosis": diagnosis.diagnosis},
                },
                timeout=self.timeout,
            )
            response.raise_for_status()
            data = response.json()
            link_url = data.get("short_url", "")
        except Exception as exc:  # transport error, timeout, non-2xx, malformed body
            return ActionResult(
                executed=False,
                result="failed",
                revenue_recovered=0,
                message=f"Razorpay Test Mode call failed: {exc}"[:300],
                gateway=self.name,
            )

        # Payment link created, but nothing here confirms the customer has
        # actually paid -- see module docstring for why this can't be "success."
        return ActionResult(
            executed=True,
            result="skipped",
            revenue_recovered=0,
            message=f"Payment link created for {action}: {link_url}"[:300],
            gateway=self.name,
        )
