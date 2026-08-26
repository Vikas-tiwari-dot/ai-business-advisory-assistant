import random

import pytest

from app.core.config import get_settings
from app.schemas.diagnosis import DiagnosisResult
from app.schemas.events import CustomerHistory, NormalizedEvent
from app.services.payment_gateway.gateway import NON_EXECUTING_ACTIONS
from app.services.payment_gateway.registry import get_gateway
from app.services.payment_gateway.simulator import RECOVERY_SUCCESS_PROBABILITY, RecoverySimulator


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


# --- Non-executing actions never touch the probability draw -----------------


@pytest.mark.parametrize("action", sorted(NON_EXECUTING_ACTIONS))
def test_non_executing_actions_are_always_skipped(action):
    sim = RecoverySimulator(rng=random.Random(1))
    result = sim.execute(action, _event(), _diagnosis(), revenue_at_risk=499900)
    assert result.executed is False
    assert result.result == "skipped"
    assert result.revenue_recovered == 0
    assert result.gateway == "simulator"


def test_non_executing_actions_never_consume_randomness():
    rng = random.Random(1)
    sim = RecoverySimulator(rng=rng)
    before = rng.getstate()
    sim.execute("STOP", _event(), _diagnosis(), revenue_at_risk=499900)
    after = rng.getstate()
    assert before == after  # no draw happened


# --- Executing actions: correct revenue on success/failure ------------------


def test_successful_execution_recovers_full_revenue_at_risk():
    # Force success deterministically with a rng that always returns 0.0
    class AlwaysSucceed(random.Random):
        def random(self):
            return 0.0

    sim = RecoverySimulator(rng=AlwaysSucceed())
    result = sim.execute("RETRY_PAYMENT", _event(), _diagnosis(diagnosis="temporary_failure"), revenue_at_risk=499900)
    assert result.executed is True
    assert result.result == "success"
    assert result.revenue_recovered == 499900


def test_failed_execution_recovers_zero_revenue():
    class AlwaysFail(random.Random):
        def random(self):
            return 0.999999

    sim = RecoverySimulator(rng=AlwaysFail())
    result = sim.execute("RETRY_PAYMENT", _event(), _diagnosis(diagnosis="temporary_failure"), revenue_at_risk=499900)
    assert result.executed is True
    assert result.result == "failed"
    assert result.revenue_recovered == 0


def test_unknown_diagnosis_category_falls_back_to_unknown_probability():
    sim = RecoverySimulator(rng=random.Random(1), probabilities={"unknown": 0.15})
    # diagnosis.diagnosis is a Literal so we can't construct an out-of-set value,
    # but we CAN pass a probabilities table that's missing a category to confirm
    # the .get(..., probabilities["unknown"]) fallback path works.
    diagnosis = _diagnosis(diagnosis="bank_decline")
    result = sim.execute("RETRY_PAYMENT", _event(), diagnosis, revenue_at_risk=100000)
    assert result.result in {"success", "failed"}  # didn't crash on missing key


# --- Determinism under a fixed seed ------------------------------------------


def test_same_seed_produces_identical_outcome_sequence():
    event = _event()
    diagnosis = _diagnosis(diagnosis="insufficient_funds")

    sim_a = RecoverySimulator(rng=random.Random(42))
    sim_b = RecoverySimulator(rng=random.Random(42))

    results_a = [sim_a.execute("RETRY_PAYMENT", event, diagnosis, revenue_at_risk=100000) for _ in range(50)]
    results_b = [sim_b.execute("RETRY_PAYMENT", event, diagnosis, revenue_at_risk=100000) for _ in range(50)]

    assert [r.result for r in results_a] == [r.result for r in results_b]


def test_different_seeds_diverge():
    event = _event()
    diagnosis = _diagnosis(diagnosis="insufficient_funds")

    sim_a = RecoverySimulator(rng=random.Random(1))
    sim_b = RecoverySimulator(rng=random.Random(2))

    results_a = [sim_a.execute("RETRY_PAYMENT", event, diagnosis, revenue_at_risk=100000).result for _ in range(200)]
    results_b = [sim_b.execute("RETRY_PAYMENT", event, diagnosis, revenue_at_risk=100000).result for _ in range(200)]

    assert results_a != results_b


# --- Statistical honesty: outcomes track configured probabilities, no faking


@pytest.mark.parametrize("category", list(RECOVERY_SUCCESS_PROBABILITY.keys()))
def test_empirical_success_rate_converges_to_configured_probability(category):
    """
    Runs a large batch and checks the *measured* success rate lands near the
    configured probability -- this is the check for spec §6's "do not fake
    successful recovery just to make metrics look good." A hardcoded
    `return "success"` would fail this test at every probability below 1.0.
    """
    sim = RecoverySimulator(rng=random.Random(7))
    diagnosis = _diagnosis(diagnosis=category)
    trials = 3000
    successes = sum(
        1 for _ in range(trials)
        if sim.execute("RETRY_PAYMENT", _event(), diagnosis, revenue_at_risk=1000).result == "success"
    )
    empirical_rate = successes / trials
    expected_rate = RECOVERY_SUCCESS_PROBABILITY[category]
    assert abs(empirical_rate - expected_rate) < 0.04, (
        f"{category}: expected ~{expected_rate}, measured {empirical_rate}"
    )


def test_custom_probabilities_override_defaults():
    sim = RecoverySimulator(rng=random.Random(1), probabilities={"temporary_failure": 1.0, "unknown": 1.0})
    diagnosis = _diagnosis(diagnosis="temporary_failure")
    results = [sim.execute("RETRY_PAYMENT", _event(), diagnosis, revenue_at_risk=100).result for _ in range(20)]
    assert all(r == "success" for r in results)


# --- Registry --------------------------------------------------------------


def test_registry_returns_simulator_by_default():
    get_settings.cache_clear()
    settings = get_settings()
    gateway = get_gateway(settings)
    assert gateway.name == "simulator"


def test_registry_returns_simulator_when_razorpay_keys_missing_even_if_flag_set():
    get_settings.cache_clear()
    settings = get_settings()
    settings.use_razorpay_test_mode = True
    settings.razorpay_key_id = None
    settings.razorpay_key_secret = None
    gateway = get_gateway(settings)
    assert gateway.name == "simulator"


def test_registry_seed_produces_reproducible_gateway_behavior():
    settings = get_settings()
    gw_a = get_gateway(settings, seed=99)
    gw_b = get_gateway(settings, seed=99)
    diagnosis = _diagnosis(diagnosis="bank_decline")
    seq_a = [gw_a.execute("RETRY_PAYMENT", _event(), diagnosis, revenue_at_risk=100).result for _ in range(30)]
    seq_b = [gw_b.execute("RETRY_PAYMENT", _event(), diagnosis, revenue_at_risk=100).result for _ in range(30)]
    assert seq_a == seq_b
