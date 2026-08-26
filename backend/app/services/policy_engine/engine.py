"""
Policy / Safety Engine (spec module 4).

This is the ONLY place in the system that decides whether a proposed recovery
action is actually allowed to execute. It is pure and deterministic -- no LLM,
no I/O -- so it is exhaustively testable and its behavior is fully predictable
under audit. Every rule here maps directly to a bullet in the spec, and every
blocked outcome is required to carry a human-readable reason (spec §4).

Rule evaluation order matters: it's a short-circuiting chain of early returns,
each rule strictly higher priority than the ones below it (e.g. "duplicate
event" pre-empts every other check, since a duplicate should never even reach
a "should we retry" decision).
"""
from app.core.config import get_settings
from app.schemas.context import CustomerContext
from app.schemas.diagnosis import DiagnosisResult
from app.schemas.events import NormalizedEvent
from app.schemas.policy import PolicyDecision
from app.schemas.recovery_action import ProposedActionResult
from app.schemas.risk import RiskAssessment

RETRY_ACTIONS = {"RETRY_PAYMENT", "SCHEDULE_RETRY"}
CONTACT_ACTIONS = {"RETRY_PAYMENT", "SCHEDULE_RETRY", "SEND_REMINDER", "OFFER_ALTERNATE_METHOD"}


def evaluate(
    proposed: ProposedActionResult,
    event: NormalizedEvent,
    context: CustomerContext,
    diagnosis: DiagnosisResult,
    risk: RiskAssessment,
    *,
    payment_already_recovered: bool = False,
    is_duplicate_event: bool = False,
) -> PolicyDecision:
    settings = get_settings()

    # Rule: duplicate event -> ignore entirely. Nothing below this matters --
    # a duplicate should never generate a fresh policy decision at all.
    if is_duplicate_event:
        return PolicyDecision(
            allowed=False,
            reason="Duplicate event; ignoring.",
            final_action="IGNORE",
            requires_human_review=False,
        )

    # Rule: never retry (or do anything) on an already-recovered payment.
    if payment_already_recovered or event.status == "recovered":
        return PolicyDecision(
            allowed=False,
            reason="Payment already recovered; no further action needed.",
            final_action="STOP",
            requires_human_review=False,
        )

    # STOP proposals never need a safeguard -- stopping is always safe.
    if proposed.action == "STOP":
        return PolicyDecision(
            allowed=True,
            reason="Stop action requires no additional safeguards.",
            final_action="STOP",
            requires_human_review=False,
        )

    # Rule: never contact a customer who has opted out.
    if event.customer_history.opted_out and proposed.action in CONTACT_ACTIONS:
        return PolicyDecision(
            allowed=False,
            reason="Customer has opted out of contact; contact actions are blocked.",
            final_action="STOP",
            requires_human_review=False,
        )

    # Rule: maximum recovery attempts reached -> no more retries, escalate.
    if context.recovery_attempts >= settings.max_recovery_attempts and proposed.action in RETRY_ACTIONS:
        return PolicyDecision(
            allowed=False,
            reason=f"Maximum recovery attempts ({settings.max_recovery_attempts}) reached.",
            final_action="ESCALATE_HUMAN",
            requires_human_review=True,
        )

    # Rule: never retry immediately after repeated failures -- escalate instead.
    if diagnosis.diagnosis == "repeated_failure" and proposed.action in RETRY_ACTIONS:
        return PolicyDecision(
            allowed=False,
            reason="Repeated failures detected; automatic retry is blocked, escalating instead.",
            final_action="ESCALATE_HUMAN",
            requires_human_review=True,
        )

    # Rule: unknown diagnosis -> always requires a human, no auto-action.
    if diagnosis.diagnosis == "unknown" and proposed.action != "ESCALATE_HUMAN":
        return PolicyDecision(
            allowed=False,
            reason="Diagnosis is unknown; requires human escalation rather than an automatic action.",
            final_action="ESCALATE_HUMAN",
            requires_human_review=True,
        )

    # Rule: low-confidence AI decision -> escalate rather than trust it blindly.
    if diagnosis.confidence < settings.low_confidence_threshold and proposed.action != "ESCALATE_HUMAN":
        return PolicyDecision(
            allowed=False,
            reason=(
                f"Diagnosis confidence {diagnosis.confidence:.2f} is below the "
                f"{settings.low_confidence_threshold} threshold; requires human escalation."
            ),
            final_action="ESCALATE_HUMAN",
            requires_human_review=True,
        )

    # Rule: never auto-execute above the configured value threshold without approval.
    if (
        proposed.expected_recovery_value >= settings.high_value_escalation_threshold
        and proposed.action != "ESCALATE_HUMAN"
    ):
        return PolicyDecision(
            allowed=False,
            reason=(
                f"Expected recovery value {proposed.expected_recovery_value} is at or above the "
                f"auto-approval threshold ({settings.high_value_escalation_threshold}); requires human approval."
            ),
            final_action="ESCALATE_HUMAN",
            requires_human_review=True,
        )

    # All gates passed (or the agent already proposed ESCALATE_HUMAN itself).
    return PolicyDecision(
        allowed=True,
        reason="Policy checks passed.",
        final_action=proposed.action,
        requires_human_review=(proposed.action == "ESCALATE_HUMAN"),
    )
