"""
LLMProvider is the only interface the rest of the app is allowed to call an AI
model through. Concrete providers (Gemini, OpenAI) implement `.complete()` and
nothing else -- they return raw text and do zero JSON parsing or validation of
their own. That work happens once, centrally, in `structured.call_structured`,
which is what actually enforces "no unvalidated LLM output reaches the policy
engine" and "retry once then deterministic fallback."

Providers must never be given write access to the database or the payment
gateway. Nothing in this package imports from app.services.payment_gateway or
app.db -- that boundary is what keeps "AI decides, policy controls, system
executes" true structurally rather than just by convention.
"""
from abc import ABC, abstractmethod


class LLMProvider(ABC):
    name: str

    @abstractmethod
    def complete(self, prompt: str, timeout: float) -> str:
        """
        Return the raw text completion for `prompt`. Must raise on timeout or
        transport failure rather than returning a partial/empty string, so
        callers can distinguish "provider errored" from "provider said
        something we couldn't parse."
        """
        raise NotImplementedError
