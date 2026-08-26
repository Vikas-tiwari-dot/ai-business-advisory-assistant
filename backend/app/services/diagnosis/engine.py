"""
AI Diagnosis Engine (spec module C).

Classifies the likely cause of a payment failure into one of the categories in
DiagnosisCategory. This is the first of the two LLM-touching services in the
pipeline (the second is RecoveryAgent, Phase 6). It never executes anything --
it returns a DiagnosisResult and stops.

Fallback contract: if AI_PROVIDER=none (no provider configured) or the model's
output fails schema validation twice (one retry, per app.services.ai.structured),
this falls back to a deterministic rule table. The fallback is not a lesser
"best effort" -- it uses the same objective signals (failure_code, attempt_number)
a human analyst would use, and is marked unambiguously so nothing downstream
mistakes it for a genuine AI judgment.
"""
from app.schemas.context import CustomerContext
from app.schemas.diagnosis import Diagnosis, DiagnosisCategory, DiagnosisResult
from app.schemas.events import NormalizedEvent
from app.schemas.risk import RiskAssessment
from app.services.ai.provider import LLMProvider
from app.services.ai.structured import call_structured

FAILURE_CODE_TO_CATEGORY: dict[str, DiagnosisCategory] = {
    "NETWORK_ERROR": "temporary_failure",
    "GATEWAY_TIMEOUT": "temporary_failure",
    "INSUFFICIENT_FUNDS": "insufficient_funds",
    "BANK_DECLINE": "bank_decline",
    "ISSUER_DECLINE": "bank_decline",
    "CARD_EXPIRED": "expired_instrument",
    "INSTRUMENT_INVALID": "expired_instrument",
    "CHECKOUT_ABANDONED": "checkout_abandonment",
    "INVOICE_OVERDUE": "overdue_invoice",
}

CATEGORY_TO_STRATEGY: dict[DiagnosisCategory, str] = {
    "temporary_failure": "retry_later",
    "insufficient_funds": "schedule_retry",
    "bank_decline": "escalate_or_stop",
    "expired_instrument": "offer_alternate_method",
    "repeated_failure": "escalate_human",
    "checkout_abandonment": "send_reminder",
    "overdue_invoice": "send_reminder",
    "unknown": "escalate_human",
}

REPEATED_FAILURE_ATTEMPT_THRESHOLD = 3
FALLBACK_NOTICE = "AI unavailable — deterministic fallback used."

ALL_CATEGORIES = sorted(CATEGORY_TO_STRATEGY.keys())


class DiagnosisEngine:
    def __init__(self, provider: LLMProvider | None, timeout: float = 8.0):
        self.provider = provider
        self.timeout = timeout

    def diagnose(
        self,
        event: NormalizedEvent,
        context: CustomerContext,
        risk: RiskAssessment,
    ) -> DiagnosisResult:
        if self.provider is None:
            # No AI was ever attempted, so there's no "invalid AI output" to speak
            # of -- schema_valid=True here just means "nothing to flag."
            return self._fallback(event, context, risk, attempted_provider=None, ai_schema_valid=True)

        prompt = self._build_prompt(event, context, risk)
        parsed, meta = call_structured(self.provider, prompt, Diagnosis, max_retries=1, timeout=self.timeout)

        if parsed is None:
            # AI was attempted and its output failed validation twice -- this is
            # the case schema_valid=False exists to flag.
            return self._fallback(
                event, context, risk, attempted_provider=self.provider.name, ai_schema_valid=False
            )

        return DiagnosisResult(
            **parsed.model_dump(),
            model_provider=self.provider.name,
            schema_valid=True,
            fallback_used=False,
            attempted_provider=self.provider.name,
        )

    # -- fallback -----------------------------------------------------------

    def _fallback(
        self,
        event: NormalizedEvent,
        context: CustomerContext,
        risk: RiskAssessment,
        *,
        attempted_provider: str | None,
        ai_schema_valid: bool,
    ) -> DiagnosisResult:
        if event.attempt_number >= REPEATED_FAILURE_ATTEMPT_THRESHOLD:
            category: DiagnosisCategory = "repeated_failure"
            confidence = 0.90  # attempt_number is an unambiguous, directly-observed signal
            reason = f"{event.attempt_number} attempts recorded; treating as repeated failure."
        elif event.failure_code in FAILURE_CODE_TO_CATEGORY:
            category = FAILURE_CODE_TO_CATEGORY[event.failure_code]
            confidence = 0.65
            reason = f"Failure code {event.failure_code} maps to {category} by rule."
        else:
            category = "unknown"
            confidence = 0.35
            reason = "Failure code not recognized by the deterministic classifier."

        return DiagnosisResult(
            diagnosis=category,
            confidence=confidence,
            reasoning_summary=f"{FALLBACK_NOTICE} {reason}"[:300],
            recommended_strategy=CATEGORY_TO_STRATEGY[category],
            model_provider="fallback_rules",
            schema_valid=ai_schema_valid,
            fallback_used=True,
            attempted_provider=attempted_provider,
        )

    # -- prompt ---------------------------------------------------------------

    def _build_prompt(self, event: NormalizedEvent, context: CustomerContext, risk: RiskAssessment) -> str:
        categories = ", ".join(ALL_CATEGORIES)
        return f"""You are a payment failure classifier for a fintech revenue recovery system.

Classify the most likely cause of this payment failure into exactly one of:
{categories}

Payment:
- amount: {event.amount} {event.currency}
- status: {event.status}
- failure_code: {event.failure_code}
- failure_reason: {event.failure_reason}
- payment_method: {event.payment_method}
- attempt_number: {event.attempt_number}

Customer context:
- segment: {context.customer_segment}
- lifetime_value: {context.lifetime_value}
- failure_rate: {context.failure_rate}
- recovery_attempts_so_far: {context.recovery_attempts}

Deterministic risk assessment (already computed, do not recompute):
- risk_score: {risk.risk_score}
- risk_category: {risk.risk_category}

Business rules:
- attempt_number >= 3 should usually be classified repeated_failure.
- If the signals genuinely do not fit any category with confidence, classify unknown
  rather than guessing -- low-confidence guesses cause more harm than an honest unknown.

Respond with ONLY a single JSON object, no prose, no markdown code fences, no text
before or after it, matching exactly this shape:

{{
  "diagnosis": "<one category from the list above>",
  "confidence": <float between 0 and 1>,
  "reasoning_summary": "<one short business-facing sentence, under 300 characters, no internal reasoning or chain-of-thought>",
  "recommended_strategy": "<short label such as retry_later, schedule_retry, offer_alternate_method, send_reminder, escalate_human, stop>"
}}"""
