from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

DiagnosisCategory = Literal[
    "temporary_failure",
    "insufficient_funds",
    "bank_decline",
    "expired_instrument",
    "repeated_failure",
    "checkout_abandonment",
    "overdue_invoice",
    "unknown",
]


class Diagnosis(BaseModel):
    """
    Exactly what the LLM must return, and nothing more. `reasoning_summary` is
    capped at 300 characters -- a structural guarantee (not just a prompt
    instruction) that no hidden chain-of-thought or verbose internal reasoning
    can slip through as "the explanation," per spec §14: "Do not expose hidden
    chain-of-thought. Store only a short business-facing explanation."
    """

    diagnosis: DiagnosisCategory
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning_summary: str = Field(max_length=300)
    recommended_strategy: str = Field(max_length=64)


class DiagnosisResult(Diagnosis):
    """Diagnosis plus provenance -- what actually produced this output and whether it's trustworthy as-is."""

    model_config = ConfigDict(protected_namespaces=())

    model_provider: str  # "gemini" | "openai" | "fallback_rules"
    schema_valid: bool  # False means the AI's own output failed validation and this is a fallback
    fallback_used: bool
    attempted_provider: str | None = None
