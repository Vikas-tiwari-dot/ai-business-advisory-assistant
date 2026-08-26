from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# The complete, closed set of actions the RecoveryAgent may ever propose.
# This Literal is the actual enforcement mechanism: Pydantic will reject any
# value outside this set, so "the LLM must never propose an arbitrary action"
# is a validation-layer guarantee, not a prompt-only request.
RecoveryActionType = Literal[
    "RETRY_PAYMENT",
    "SEND_REMINDER",
    "OFFER_ALTERNATE_METHOD",
    "SCHEDULE_RETRY",
    "ESCALATE_HUMAN",
    "STOP",
]

Priority = Literal["low", "medium", "high"]


class ProposedAction(BaseModel):
    """
    Exactly what the LLM (or fallback) may return. This object is a *proposal*
    only -- nothing in this schema or the agent that produces it has any way to
    cause money to move. See app/services/policy_engine (Phase 7) for the gate
    that turns a proposal into something that's actually allowed to execute.
    """

    action: RecoveryActionType
    priority: Priority
    reason: str = Field(max_length=300)
    expected_recovery_value: int = Field(ge=0, description="Minor units (paise)")
    confidence: float = Field(ge=0.0, le=1.0)


class ProposedActionResult(ProposedAction):
    model_config = ConfigDict(protected_namespaces=())

    model_provider: str  # "gemini" | "openai" | "fallback_rules"
    schema_valid: bool  # False means a real AI attempt failed validation and this is a fallback
    fallback_used: bool
    attempted_provider: str | None = None
