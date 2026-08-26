"""
RecoverySimulator (spec module 6). This is what makes the whole demo runnable
with zero external credentials -- it's the default gateway whenever Razorpay
Test Mode isn't configured (see registry.get_gateway).

Success probabilities are keyed by *diagnosis category*, not by action, and
deliberately mirror the ground-truth `recover_prob` values used to generate
the synthetic dataset in scripts/generate_data.py::CATEGORY_CONFIG -- so a
batch run through the full pipeline should converge toward those same
recovery rates over enough records, which is what makes the evaluation
metrics in Phase 10 meaningful rather than circular. The two tables are kept
independently defined (not imported from one another) so a change to one
doesn't silently drift the other without a human noticing.

Per spec §6: "Do not fake successful recovery just to make metrics look
good." This module draws a real random number against a real, configurable
probability every time -- there is no code path that returns "success"
unconditionally.

Deterministic reproducibility: pass a seeded `random.Random` instance and
every run over the same event sequence produces the same outcomes. This
matters for Phase 10's evaluation, which needs stable, reproducible numbers.
"""
import random

from app.schemas.diagnosis import DiagnosisResult
from app.schemas.events import NormalizedEvent
from app.schemas.execution import ActionResult
from app.schemas.policy import ResolvedAction
from app.services.payment_gateway.gateway import NON_EXECUTING_ACTIONS, PaymentGateway

RECOVERY_SUCCESS_PROBABILITY: dict[str, float] = {
    "temporary_failure": 0.75,
    "insufficient_funds": 0.30,
    "bank_decline": 0.10,
    "expired_instrument": 0.55,  # only reachable via OFFER_ALTERNATE_METHOD in practice
    "repeated_failure": 0.05,    # policy should already have redirected these to ESCALATE_HUMAN
    "checkout_abandonment": 0.40,
    "overdue_invoice": 0.50,
    "unknown": 0.15,             # conservative default for an unrecognized category
}


class RecoverySimulator(PaymentGateway):
    name = "simulator"

    def __init__(self, rng: random.Random | None = None, probabilities: dict[str, float] | None = None):
        self.rng = rng or random.Random()
        self.probabilities = probabilities or RECOVERY_SUCCESS_PROBABILITY

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

        probability = self.probabilities.get(diagnosis.diagnosis, self.probabilities["unknown"])
        succeeded = self.rng.random() < probability

        if succeeded:
            return ActionResult(
                executed=True,
                result="success",
                revenue_recovered=revenue_at_risk,
                message=f"{action} succeeded (simulated, category={diagnosis.diagnosis}, p={probability}).",
                gateway=self.name,
            )

        return ActionResult(
            executed=True,
            result="failed",
            revenue_recovered=0,
            message=f"{action} did not recover the payment (simulated, category={diagnosis.diagnosis}, p={probability}).",
            gateway=self.name,
        )
