"""
Revenue Risk Detector (spec module B).

Deterministic only -- this module never calls an LLM. Its job is narrow: given a
normalized event, decide (a) is any revenue actually at risk, (b) how severe is
that risk, and (c) how confident are we in that read. It does NOT diagnose *why*
the payment failed (that's DiagnosisEngine, Phase 5) and it does NOT decide what
to do about it (that's RecoveryAgent + PolicyEngine, Phases 6-7).

Scoring is a simple, auditable weighted sum of named signals rather than a black
box -- every RiskAssessment carries the `signals` dict that produced its score,
so the audit trail can show exactly why a number came out the way it did.
"""
from app.schemas.events import NormalizedEvent
from app.schemas.risk import RiskAssessment

# Base severity per failure code: how likely, all else equal, is this kind of
# failure to represent revenue that's genuinely at risk of being lost (as
# opposed to a trivial blip). This is NOT a probability of recovery -- that
# distinction matters and is diagnosed later by the AI layer.
FAILURE_SEVERITY: dict[str, float] = {
    "NETWORK_ERROR": 0.30,
    "GATEWAY_TIMEOUT": 0.30,
    "INSUFFICIENT_FUNDS": 0.55,
    "BANK_DECLINE": 0.75,
    "ISSUER_DECLINE": 0.75,
    "CARD_EXPIRED": 0.60,
    "INSTRUMENT_INVALID": 0.60,
    "CHECKOUT_ABANDONED": 0.50,
    "INVOICE_OVERDUE": 0.45,
}
UNKNOWN_FAILURE_SEVERITY = 0.65  # unfamiliar failure codes are treated cautiously

ATTEMPT_PENALTY_PER_RETRY = 0.10  # each attempt beyond the first nudges risk up
ATTEMPT_PENALTY_CAP = 0.30

RISK_CATEGORY_BY_FAILURE_CODE: dict[str, str] = {
    "CHECKOUT_ABANDONED": "checkout_abandonment",
    "INVOICE_OVERDUE": "overdue_invoice",
}

NON_AT_RISK_STATUSES = {"created", "recovered"}


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


def assess_risk(event: NormalizedEvent) -> RiskAssessment:
    """
    Pure function: same event in -> same RiskAssessment out, always. No I/O,
    no randomness, no external calls -- this is what makes it safe to run in a
    tight loop over a batch without cost or latency concerns.
    """
    if event.status in NON_AT_RISK_STATUSES:
        return RiskAssessment(
            risk_score=0.0,
            revenue_at_risk=0,
            risk_category="none",
            confidence=1.0,
            signals={"reason": f"status={event.status} is not at-risk"},
        )

    # "pending" (e.g. a payment mid-flight, or an unresolved invoice not yet
    # overdue) carries some risk but no failure signal to size it against --
    # score it low-confidence and modest, and let the human/AI layers refine it.
    if event.status == "pending" and event.failure_code is None:
        return RiskAssessment(
            risk_score=0.20,
            revenue_at_risk=event.amount,
            risk_category="none",
            confidence=0.40,
            signals={"reason": "pending with no failure signal yet"},
        )

    failure_code = event.failure_code or "UNKNOWN"
    severity = FAILURE_SEVERITY.get(failure_code, UNKNOWN_FAILURE_SEVERITY)

    attempt_penalty = min(
        ATTEMPT_PENALTY_CAP, max(0, event.attempt_number - 1) * ATTEMPT_PENALTY_PER_RETRY
    )

    risk_score = _clamp(severity + attempt_penalty)

    risk_category = RISK_CATEGORY_BY_FAILURE_CODE.get(failure_code, "payment_failure")

    # Confidence reflects how well-understood the signal is: a known failure
    # code with a normal attempt count is high-confidence; an unrecognized code,
    # or an unusually high attempt count (suggests messy/edge-case data), pulls
    # confidence down so downstream policy is more likely to escalate it.
    confidence = 0.95 if failure_code in FAILURE_SEVERITY else 0.55
    if event.attempt_number > 5:
        confidence = _clamp(confidence - 0.20)

    revenue_at_risk = event.amount if event.status in {"failed", "unrecoverable"} else 0
    # `unrecoverable` still represents revenue that WAS at risk and wasn't saved --
    # useful for the dashboard's at-risk trendline even though no action applies.
    if event.status == "unrecoverable":
        risk_category = "payment_failure" if risk_category == "none" else risk_category

    return RiskAssessment(
        risk_score=round(risk_score, 4),
        revenue_at_risk=revenue_at_risk,
        risk_category=risk_category,
        confidence=round(confidence, 4),
        signals={
            "failure_code": failure_code,
            "severity_base": severity,
            "attempt_number": event.attempt_number,
            "attempt_penalty": round(attempt_penalty, 4),
            "known_failure_code": failure_code in FAILURE_SEVERITY,
        },
    )
