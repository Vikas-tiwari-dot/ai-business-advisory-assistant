import pytest

from app.schemas.context import CustomerContext
from app.schemas.diagnosis import DiagnosisResult
from app.schemas.events import CustomerHistory, NormalizedEvent
from app.schemas.recovery_action import ProposedActionResult
from app.services.policy_engine.engine import evaluate
from app.services.risk_detector.detector import assess_risk


def _event(**overrides) -> NormalizedEvent:
    base = dict(
        event_id="evt_1",
        customer_id="cust_1",
        payment_id="pay_1",
        amount=499900,
        currency="INR",
        status="failed",
        failure_code="NETWORK_ERROR",
        failure_reason="Temporary network error",
        timestamp="2026-08-20T10:00:00Z",
        payment_method="card",
        attempt_number=1,
        customer_history=CustomerHistory(),
    )
    base.update(overrides)
    return NormalizedEvent(**base)


def _context(**overrides) -> CustomerContext:
    base = dict(customer_segment="standard", lifetime_value=200000, failure_rate=0.1, recovery_attempts=0)
    base.update(overrides)
    return CustomerContext(**base)


def _diagnosis(**overrides) -> DiagnosisResult:
    base = dict(
        diagnosis="temporary_failure",
        confidence=0.85,
        reasoning_summary="Looks transient.",
        recommended_strategy="retry_later",
        model_provider="fallback_rules",
        schema_valid=True,
        fallback_used=True,
        attempted_provider=None,
    )
    base.update(overrides)
    return DiagnosisResult(**base)


def _proposed(**overrides) -> ProposedActionResult:
    base = dict(
        action="RETRY_PAYMENT",
        priority="medium",
        reason="Transient failure, retry now.",
        expected_recovery_value=499900,
        confidence=0.8,
        model_provider="fallback_rules",
        schema_valid=True,
        fallback_used=True,
        attempted_provider=None,
    )
    base.update(overrides)
    return ProposedActionResult(**base)


def _evaluate(event=None, context=None, diagnosis=None, proposed=None, risk=None, **kwargs):
    event = event or _event()
    context = context or _context()
    diagnosis = diagnosis or _diagnosis()
    proposed = proposed or _proposed()
    risk = risk or assess_risk(event)
    return evaluate(proposed, event, context, diagnosis, risk, **kwargs)


# --- Every blocked decision carries {"allowed": false, "reason": ...} ------


def test_blocked_decision_always_has_allowed_false_and_a_reason():
    decision = _evaluate(is_duplicate_event=True)
    assert decision.allowed is False
    assert isinstance(decision.reason, str) and len(decision.reason) > 0


# --- Rule: duplicate event -> ignore -----------------------------------------


def test_duplicate_event_is_ignored_regardless_of_proposed_action():
    decision = _evaluate(proposed=_proposed(action="STOP"), is_duplicate_event=True)
    assert decision.allowed is False
    assert decision.final_action == "IGNORE"
    assert decision.requires_human_review is False


def test_duplicate_check_preempts_every_other_rule():
    # Even a proposal that would otherwise be blocked for other reasons still
    # resolves to IGNORE, not to whatever the other rule would have said.
    decision = _evaluate(
        context=_context(recovery_attempts=99),
        diagnosis=_diagnosis(diagnosis="unknown", confidence=0.01),
        is_duplicate_event=True,
    )
    assert decision.final_action == "IGNORE"


# --- Rule: already recovered -> stop -----------------------------------------


def test_already_recovered_payment_blocks_any_action():
    decision = _evaluate(payment_already_recovered=True)
    assert decision.allowed is False
    assert decision.final_action == "STOP"
    assert "already recovered" in decision.reason.lower()


def test_status_recovered_blocks_even_without_explicit_flag():
    event = _event(status="recovered", failure_code=None, failure_reason=None)
    decision = _evaluate(event=event)
    assert decision.allowed is False
    assert decision.final_action == "STOP"


def test_never_retry_a_successful_payment():
    event = _event(status="recovered", failure_code=None, failure_reason=None)
    decision = _evaluate(event=event, proposed=_proposed(action="RETRY_PAYMENT"))
    assert decision.allowed is False
    assert decision.final_action == "STOP"


# --- Rule: opt-out blocks contact actions ------------------------------------


@pytest.mark.parametrize("action", ["RETRY_PAYMENT", "SCHEDULE_RETRY", "SEND_REMINDER", "OFFER_ALTERNATE_METHOD"])
def test_opted_out_customer_blocks_every_contact_action(action):
    event = _event(customer_history=CustomerHistory(opted_out=True))
    decision = _evaluate(event=event, proposed=_proposed(action=action))
    assert decision.allowed is False
    assert decision.final_action == "STOP"
    assert "opted out" in decision.reason.lower()


def test_opted_out_customer_does_not_block_escalation():
    event = _event(customer_history=CustomerHistory(opted_out=True))
    decision = _evaluate(event=event, proposed=_proposed(action="ESCALATE_HUMAN", expected_recovery_value=0))
    assert decision.allowed is True  # escalating isn't "contact"


# --- Rule: maximum recovery attempts -----------------------------------------


def test_max_recovery_attempts_blocks_retry():
    decision = _evaluate(context=_context(recovery_attempts=3), proposed=_proposed(action="RETRY_PAYMENT"))
    assert decision.allowed is False
    assert decision.final_action == "ESCALATE_HUMAN"
    assert "maximum recovery attempts" in decision.reason.lower()


def test_max_recovery_attempts_blocks_scheduled_retry_too():
    decision = _evaluate(context=_context(recovery_attempts=3), proposed=_proposed(action="SCHEDULE_RETRY"))
    assert decision.allowed is False
    assert decision.final_action == "ESCALATE_HUMAN"


def test_below_max_attempts_does_not_block_retry():
    decision = _evaluate(context=_context(recovery_attempts=2), proposed=_proposed(action="RETRY_PAYMENT"))
    assert decision.allowed is True


def test_max_attempts_does_not_block_non_retry_actions():
    decision = _evaluate(
        context=_context(recovery_attempts=5),
        proposed=_proposed(action="SEND_REMINDER"),
        diagnosis=_diagnosis(diagnosis="checkout_abandonment"),
    )
    assert decision.allowed is True


# --- Rule: never retry immediately after repeated failures ------------------


@pytest.mark.parametrize("action", ["RETRY_PAYMENT", "SCHEDULE_RETRY"])
def test_repeated_failure_diagnosis_blocks_retry(action):
    decision = _evaluate(
        diagnosis=_diagnosis(diagnosis="repeated_failure", confidence=0.9),
        proposed=_proposed(action=action),
    )
    assert decision.allowed is False
    assert decision.final_action == "ESCALATE_HUMAN"
    assert "repeated failures" in decision.reason.lower()


def test_repeated_failure_diagnosis_does_not_block_escalation():
    decision = _evaluate(
        diagnosis=_diagnosis(diagnosis="repeated_failure", confidence=0.9),
        proposed=_proposed(action="ESCALATE_HUMAN", expected_recovery_value=0),
    )
    assert decision.allowed is True


# --- Rule: unknown diagnosis -> escalate -------------------------------------


def test_unknown_diagnosis_forces_escalation():
    decision = _evaluate(
        diagnosis=_diagnosis(diagnosis="unknown", confidence=0.5),
        proposed=_proposed(action="RETRY_PAYMENT"),
    )
    assert decision.allowed is False
    assert decision.final_action == "ESCALATE_HUMAN"
    assert "unknown" in decision.reason.lower()


def test_unknown_diagnosis_with_already_escalating_proposal_is_allowed():
    decision = _evaluate(
        diagnosis=_diagnosis(diagnosis="unknown", confidence=0.5),
        proposed=_proposed(action="ESCALATE_HUMAN", expected_recovery_value=0),
    )
    assert decision.allowed is True
    assert decision.requires_human_review is True


# --- Rule: low-confidence AI decision -> escalate ----------------------------


def test_low_confidence_diagnosis_forces_escalation():
    decision = _evaluate(
        diagnosis=_diagnosis(diagnosis="temporary_failure", confidence=0.10),
        proposed=_proposed(action="RETRY_PAYMENT"),
    )
    assert decision.allowed is False
    assert decision.final_action == "ESCALATE_HUMAN"
    assert "confidence" in decision.reason.lower()


def test_confidence_at_or_above_threshold_does_not_force_escalation():
    decision = _evaluate(
        diagnosis=_diagnosis(diagnosis="temporary_failure", confidence=0.55),  # equals default threshold
        proposed=_proposed(action="RETRY_PAYMENT"),
    )
    assert decision.allowed is True


# --- Rule: never execute above threshold without approval -------------------


def test_high_value_action_requires_human_approval():
    decision = _evaluate(
        proposed=_proposed(action="RETRY_PAYMENT", expected_recovery_value=1_500_000),  # > default 1,000,000
    )
    assert decision.allowed is False
    assert decision.final_action == "ESCALATE_HUMAN"
    assert "threshold" in decision.reason.lower()


def test_below_threshold_action_does_not_require_approval():
    decision = _evaluate(
        proposed=_proposed(action="RETRY_PAYMENT", expected_recovery_value=500_000),
    )
    assert decision.allowed is True


# --- Rule: STOP is always trivially allowed ----------------------------------


def test_stop_action_is_always_allowed():
    decision = _evaluate(
        context=_context(recovery_attempts=99),
        diagnosis=_diagnosis(diagnosis="unknown", confidence=0.0),
        proposed=_proposed(action="STOP", expected_recovery_value=0),
    )
    assert decision.allowed is True
    assert decision.final_action == "STOP"
    assert decision.requires_human_review is False


# --- Happy path: everything clean -> approved as proposed -------------------


def test_clean_proposal_is_approved_exactly_as_proposed():
    decision = _evaluate()  # all defaults: temporary_failure, confidence 0.85, RETRY_PAYMENT, no red flags
    assert decision.allowed is True
    assert decision.final_action == "RETRY_PAYMENT"
    assert decision.reason == "Policy checks passed."
    assert decision.requires_human_review is False


def test_escalate_human_proposal_is_approved_and_flagged_for_review():
    decision = _evaluate(proposed=_proposed(action="ESCALATE_HUMAN", expected_recovery_value=0))
    assert decision.allowed is True
    assert decision.final_action == "ESCALATE_HUMAN"
    assert decision.requires_human_review is True


# --- Rule priority: earlier rules pre-empt later ones ------------------------


def test_opt_out_takes_priority_over_max_attempts_reason():
    """Both rules would fire; opt-out should win and produce the opt-out reason."""
    event = _event(customer_history=CustomerHistory(opted_out=True))
    decision = _evaluate(
        event=event,
        context=_context(recovery_attempts=5),
        proposed=_proposed(action="RETRY_PAYMENT"),
    )
    assert "opted out" in decision.reason.lower()
    assert "maximum recovery attempts" not in decision.reason.lower()
