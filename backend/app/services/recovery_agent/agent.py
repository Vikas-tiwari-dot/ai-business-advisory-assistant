"""
Recovery Decision Agent (spec module E).

STRUCTURAL BOUNDARY, not just a convention: this module must never import
anything that can move money, write to the database, or contact a customer.
It receives typed inputs (event, context, diagnosis, risk) and returns a
ProposedActionResult -- a value object -- and nothing else. There is no
session, no gateway client, no audit writer in this file's import list, on
purpose. tests/unit/test_recovery_agent.py statically asserts this via AST
inspection so the boundary can't quietly erode in a later edit.

The agent proposes. app/services/policy_engine (Phase 7) decides whether the
proposal is allowed. app/services/payment_gateway (Phase 8) is the only thing
that ever actually executes an action. This file sits strictly upstream of
both and knows about neither.
"""
from app.core.config import get_settings
from app.schemas.context import CustomerContext
from app.schemas.diagnosis import DiagnosisResult
from app.schemas.events import NormalizedEvent
from app.schemas.recovery_action import ProposedAction, ProposedActionResult, RecoveryActionType
from app.schemas.risk import RiskAssessment
from app.services.ai.provider import LLMProvider
from app.services.ai.structured import call_structured

DIAGNOSIS_TO_ACTION: dict[str, RecoveryActionType] = {
    "temporary_failure": "RETRY_PAYMENT",
    "insufficient_funds": "SCHEDULE_RETRY",
    "bank_decline": "ESCALATE_HUMAN",
    "expired_instrument": "OFFER_ALTERNATE_METHOD",
    "repeated_failure": "ESCALATE_HUMAN",
    "checkout_abandonment": "SEND_REMINDER",
    "overdue_invoice": "SEND_REMINDER",
    "unknown": "ESCALATE_HUMAN",
}

FALLBACK_NOTICE = "AI unavailable — deterministic fallback used."

CONTACT_ACTIONS: set[RecoveryActionType] = {"SEND_REMINDER", "RETRY_PAYMENT", "SCHEDULE_RETRY", "OFFER_ALTERNATE_METHOD"}


class RecoveryAgent:
    def __init__(self, provider: LLMProvider | None, timeout: float = 8.0):
        self.provider = provider
        self.timeout = timeout

    def propose(
        self,
        event: NormalizedEvent,
        context: CustomerContext,
        diagnosis: DiagnosisResult,
        risk: RiskAssessment,
    ) -> ProposedActionResult:
        if self.provider is None:
            return self._fallback(event, context, diagnosis, risk, attempted_provider=None, ai_schema_valid=True)

        prompt = self._build_prompt(event, context, diagnosis, risk)
        parsed, meta = call_structured(self.provider, prompt, ProposedAction, max_retries=1, timeout=self.timeout)

        if parsed is None:
            return self._fallback(
                event, context, diagnosis, risk, attempted_provider=self.provider.name, ai_schema_valid=False
            )

        return ProposedActionResult(
            **parsed.model_dump(),
            model_provider=self.provider.name,
            schema_valid=True,
            fallback_used=False,
            attempted_provider=self.provider.name,
        )

    # -- fallback ---------------------------------------------------------

    def _fallback(
        self,
        event: NormalizedEvent,
        context: CustomerContext,
        diagnosis: DiagnosisResult,
        risk: RiskAssessment,
        *,
        attempted_provider: str | None,
        ai_schema_valid: bool,
    ) -> ProposedActionResult:
        settings = get_settings()

        # Opt-out is checked here as a sensible default proposal, but this is
        # NOT the enforcement point -- the policy engine (Phase 7) independently
        # blocks any contact action against an opted-out customer regardless of
        # what the agent (AI or fallback) proposes. Defense in depth.
        if event.customer_history.opted_out:
            action: RecoveryActionType = "STOP"
            reason = "Customer has opted out of contact; no recovery action proposed."
            confidence = 0.99
        elif diagnosis.confidence < settings.low_confidence_threshold:
            action = "ESCALATE_HUMAN"
            reason = f"Diagnosis confidence {diagnosis.confidence:.2f} is below threshold; needs human review."
            confidence = 1.0 - diagnosis.confidence
        elif context.recovery_attempts >= settings.max_recovery_attempts:
            action = "ESCALATE_HUMAN"
            reason = f"{context.recovery_attempts} recovery attempts already made; escalating rather than retrying further."
            confidence = 0.95
        else:
            action = DIAGNOSIS_TO_ACTION.get(diagnosis.diagnosis, "ESCALATE_HUMAN")
            reason = f"Diagnosis '{diagnosis.diagnosis}' maps to {action} by rule."
            confidence = round(diagnosis.confidence * 0.9, 4)  # slightly discount vs. the diagnosis's own confidence

        priority = self._compute_priority(context, risk)
        expected_recovery_value = risk.revenue_at_risk if action not in {"STOP", "ESCALATE_HUMAN"} else 0
        # Escalated/high-value cases still have money on the table even though
        # this agent isn't the one that will recover it -- record it as
        # potential value for the queue's "expected recovery value" column.
        if action == "ESCALATE_HUMAN":
            expected_recovery_value = risk.revenue_at_risk

        return ProposedActionResult(
            action=action,
            priority=priority,
            reason=f"{FALLBACK_NOTICE} {reason}"[:300],
            expected_recovery_value=expected_recovery_value,
            confidence=confidence,
            model_provider="fallback_rules",
            schema_valid=ai_schema_valid,
            fallback_used=True,
            attempted_provider=attempted_provider,
        )

    @staticmethod
    def _compute_priority(context: CustomerContext, risk: RiskAssessment) -> str:
        settings = get_settings()
        if context.customer_segment == "high_value" or risk.revenue_at_risk >= settings.high_value_escalation_threshold:
            return "high"
        if risk.risk_score >= 0.5:
            return "medium"
        return "low"

    # -- prompt -------------------------------------------------------------

    def _build_prompt(
        self,
        event: NormalizedEvent,
        context: CustomerContext,
        diagnosis: DiagnosisResult,
        risk: RiskAssessment,
    ) -> str:
        settings = get_settings()
        allowed_actions = ", ".join(
            ["RETRY_PAYMENT", "SEND_REMINDER", "OFFER_ALTERNATE_METHOD", "SCHEDULE_RETRY", "ESCALATE_HUMAN", "STOP"]
        )
        return f"""You are a bounded recovery-action recommender for a fintech revenue recovery system.

You may ONLY propose one of these exact actions -- nothing else is valid:
{allowed_actions}

You are proposing an action, not executing one. A separate deterministic policy
engine will decide whether your proposal is actually allowed to run.

Payment:
- amount: {event.amount} {event.currency}
- attempt_number: {event.attempt_number}
- payment_method: {event.payment_method}

Diagnosis (already computed, do not re-diagnose):
- diagnosis: {diagnosis.diagnosis}
- diagnosis_confidence: {diagnosis.confidence}
- recommended_strategy_hint: {diagnosis.recommended_strategy}

Risk assessment (already computed):
- risk_score: {risk.risk_score}
- revenue_at_risk: {risk.revenue_at_risk}

Customer context:
- segment: {context.customer_segment}
- failure_rate: {context.failure_rate}
- recovery_attempts_so_far: {context.recovery_attempts}
- opted_out_of_contact: {event.customer_history.opted_out}

Business rules you must respect:
- If opted_out_of_contact is true, you must propose STOP -- never propose any contact action.
- If diagnosis_confidence is below {settings.low_confidence_threshold}, propose ESCALATE_HUMAN.
- If recovery_attempts_so_far is already at or above {settings.max_recovery_attempts}, propose ESCALATE_HUMAN, not another retry.
- expected_recovery_value should be 0 for STOP, and may equal revenue_at_risk otherwise.

Respond with ONLY a single JSON object, no prose, no markdown fences, matching
exactly this shape:

{{
  "action": "<one of the six actions above, exact spelling>",
  "priority": "<low|medium|high>",
  "reason": "<one short business-facing sentence, under 300 characters>",
  "expected_recovery_value": <integer, minor units>,
  "confidence": <float between 0 and 1>
}}"""
