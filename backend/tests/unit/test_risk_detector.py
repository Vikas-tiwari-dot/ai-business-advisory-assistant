import ast
import inspect

import pytest

from app.schemas.events import CustomerHistory, NormalizedEvent
from app.services.risk_detector import detector
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


# --- No-LLM guarantee -------------------------------------------------------


def test_module_makes_no_network_or_llm_calls():
    """
    Static guard: the risk_detector module source must not reference any of the
    known AI-call entry points. This is a cheap tripwire, not a substitute for
    the architectural separation, but it catches an accidental import early.
    """
    source = inspect.getsource(detector)
    forbidden = ["openai", "gemini", "requests.post", "httpx.post", "call_structured", "LLMProvider"]
    for term in forbidden:
        assert term not in source, f"risk_detector must never reference {term!r}"


def test_module_has_no_llm_related_imports():
    tree = ast.parse(inspect.getsource(detector))
    imported_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_names.add(node.module)
    banned = {"openai", "google.generativeai", "app.services.ai"}
    assert imported_names.isdisjoint(banned)


# --- Non-at-risk statuses ----------------------------------------------------


@pytest.mark.parametrize("status", ["created", "recovered"])
def test_non_failed_statuses_are_not_at_risk(status):
    event = _event(status=status, failure_code=None, failure_reason=None)
    result = assess_risk(event)
    assert result.risk_category == "none"
    assert result.risk_score == 0.0
    assert result.revenue_at_risk == 0
    assert result.confidence == 1.0


def test_pending_with_no_failure_signal_is_low_confidence():
    event = _event(status="pending", failure_code=None, failure_reason=None)
    result = assess_risk(event)
    assert result.confidence < 0.5
    assert result.revenue_at_risk == event.amount


# --- Category coverage (mirrors scripts/generate_data.py CATEGORY_CONFIG) --


def test_temporary_failure_is_low_severity():
    event = _event(failure_code="NETWORK_ERROR", attempt_number=1)
    result = assess_risk(event)
    assert result.risk_category == "payment_failure"
    assert result.risk_score < 0.4
    assert result.revenue_at_risk == event.amount


def test_insufficient_funds_is_mid_severity():
    event = _event(failure_code="INSUFFICIENT_FUNDS", attempt_number=1)
    result = assess_risk(event)
    assert 0.4 <= result.risk_score < 0.7


def test_bank_decline_is_high_severity():
    event = _event(failure_code="BANK_DECLINE", attempt_number=1)
    result = assess_risk(event)
    assert result.risk_score >= 0.7


def test_expired_instrument_is_mid_high_severity():
    event = _event(failure_code="CARD_EXPIRED", attempt_number=1)
    result = assess_risk(event)
    assert 0.5 <= result.risk_score < 0.75


def test_repeated_failure_attempt_number_increases_score():
    low_attempt = assess_risk(_event(failure_code="BANK_DECLINE", attempt_number=1))
    high_attempt = assess_risk(_event(failure_code="BANK_DECLINE", attempt_number=4))
    assert high_attempt.risk_score > low_attempt.risk_score
    assert high_attempt.risk_score <= 1.0


def test_checkout_abandonment_maps_to_its_own_category():
    event = _event(failure_code="CHECKOUT_ABANDONED", attempt_number=1)
    result = assess_risk(event)
    assert result.risk_category == "checkout_abandonment"


def test_overdue_invoice_maps_to_its_own_category():
    event = _event(failure_code="INVOICE_OVERDUE", attempt_number=1)
    result = assess_risk(event)
    assert result.risk_category == "overdue_invoice"


# --- Edge cases --------------------------------------------------------------


def test_unknown_failure_code_gets_cautious_treatment():
    event = _event(failure_code="SOME_NEW_CODE_NOT_IN_TABLE", attempt_number=1)
    result = assess_risk(event)
    assert result.confidence < 0.7  # low confidence -> policy engine should escalate
    assert result.signals["known_failure_code"] is False


def test_unusually_high_attempt_count_further_lowers_confidence():
    normal = assess_risk(_event(failure_code="BANK_DECLINE", attempt_number=2))
    extreme = assess_risk(_event(failure_code="BANK_DECLINE", attempt_number=9))
    assert extreme.confidence < normal.confidence


def test_revenue_at_risk_equals_amount_for_failed_status():
    event = _event(amount=1234500, status="failed")
    result = assess_risk(event)
    assert result.revenue_at_risk == 1234500


def test_unrecoverable_status_still_counts_as_prior_at_risk_revenue():
    event = _event(status="unrecoverable", failure_code="BANK_DECLINE")
    result = assess_risk(event)
    assert result.revenue_at_risk == event.amount
    assert result.risk_category == "payment_failure"


def test_deterministic_same_input_same_output():
    event = _event(failure_code="INSUFFICIENT_FUNDS", attempt_number=2)
    r1 = assess_risk(event)
    r2 = assess_risk(event)
    assert r1 == r2


def test_score_and_confidence_always_within_bounds():
    for code in list(detector.FAILURE_SEVERITY) + ["UNKNOWN_CODE"]:
        for attempt in [1, 2, 5, 10]:
            result = assess_risk(_event(failure_code=code, attempt_number=attempt))
            assert 0.0 <= result.risk_score <= 1.0
            assert 0.0 <= result.confidence <= 1.0
