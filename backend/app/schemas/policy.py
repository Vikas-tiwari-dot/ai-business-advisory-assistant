from typing import Literal

from pydantic import BaseModel, Field

# Everything a proposed action can resolve to after policy review. IGNORE is
# not a recovery action at all -- it's what happens to duplicate events, which
# never should have reached policy review as a "decision" in the first place.
ResolvedAction = Literal[
    "RETRY_PAYMENT",
    "SEND_REMINDER",
    "OFFER_ALTERNATE_METHOD",
    "SCHEDULE_RETRY",
    "ESCALATE_HUMAN",
    "STOP",
    "IGNORE",
]


class PolicyDecision(BaseModel):
    """
    `allowed` answers one specific question: was the action the agent proposed
    permitted to execute exactly as proposed? False does not mean "nothing
    happens" -- `final_action` says what happens instead (usually
    ESCALATE_HUMAN or STOP, never silently nothing unless it's IGNORE for a
    duplicate event).
    """

    allowed: bool
    reason: str = Field(max_length=300)
    final_action: ResolvedAction
    requires_human_review: bool
