import random

import pytest

from app.services.diagnosis.engine import DiagnosisEngine
from app.services.evaluation.engine import EvaluationEngine
from app.services.payment_gateway.simulator import RecoverySimulator
from app.services.recovery_agent.agent import RecoveryAgent


def _record(**overrides) -> dict:
    base = {
        "event_id": "evt_1",
        "customer_id": "cust_1",
        "payment_id": "pay_1",
        "amount": 499900,
        "currency": "INR",
        "status": "failed",
        "failure_code": "NETWORK_ERROR",
        "failure_reason": "Temporary network error",
        "timestamp": "2026-08-20T10:00:00Z",
        "payment_method": "card",
        "attempt_number": 1,
        "customer_history": {
            "previous_successful_payments": 5,
            "previous_failed_payments": 1,
            "lifetime_value": 200000,
            "last_successful_payment_at": None,
            "opted_out": False,
        },
        "ground_truth": {
            "category": "temporary_failure",
            "is_recoverable": True,
            "recommended_strategy": "retry_later",
            "dataset_split": "holdout",
        },
    }
    base.update(overrides)
    return base


def _engine(seed: int = 1) -> EvaluationEngine:
    return EvaluationEngine(
        DiagnosisEngine(provider=None),
        RecoveryAgent(provider=None),
        RecoverySimulator(rng=random.Random(seed)),
    )


# --- Split enforcement: the literal exit criterion --------------------------


def test_raises_on_explicit_train_split_argument():
    engine = _engine()
    with pytest.raises(ValueError, match="holdout"):
        engine.evaluate([_record()], dataset_split="train")


def test_raises_on_empty_holdout_after_filtering():
    engine = _engine()
    train_only = [_record(ground_truth={**_record()["ground_truth"], "dataset_split": "train"})]
    with pytest.raises(ValueError, match="No holdout records"):
        engine.evaluate(train_only)


def test_filters_out_train_rows_even_from_a_mixed_batch():
    engine = _engine()
    holdout_record = _record(event_id="evt_holdout")
    train_record = _record(
        event_id="evt_train",
        ground_truth={**_record()["ground_truth"], "dataset_split": "train"},
    )
    metrics = engine.evaluate([holdout_record, train_record])
    assert metrics.total_records == 1  # only the holdout row was scored


def test_raises_on_completely_empty_input():
    engine = _engine()
    with pytest.raises(ValueError):
        engine.evaluate([])


# --- Basic aggregation correctness ------------------------------------------


def test_total_records_and_revenue_computed_correctly():
    engine = _engine()
    records = [_record(event_id=f"evt_{i}", payment_id=f"pay_{i}", amount=100000) for i in range(5)]
    metrics = engine.evaluate(records)
    assert metrics.total_records == 5
    assert metrics.total_revenue == 500000


def test_recovery_rate_zero_when_no_revenue_at_risk():
    engine = _engine()
    record = _record(status="recovered", failure_code=None, failure_reason=None, ground_truth={
        "category": "already_successful", "is_recoverable": None,
        "recommended_strategy": "stop", "dataset_split": "holdout",
    })
    metrics = engine.evaluate([record])
    assert metrics.revenue_at_risk == 0
    assert metrics.recovery_rate == 0.0


def test_control_records_excluded_from_scored_count():
    engine = _engine()
    control = _record(
        event_id="evt_control", status="recovered", failure_code=None, failure_reason=None,
        ground_truth={"category": "already_successful", "is_recoverable": None,
                       "recommended_strategy": "stop", "dataset_split": "holdout"},
    )
    normal = _record(event_id="evt_normal")
    metrics = engine.evaluate([control, normal])
    assert metrics.total_records == 2  # both counted in the batch...
    assert metrics.scored_record_count == 1  # ...but only one scored for accuracy/FP/FN


# --- False positive / false negative definitions ----------------------------


def test_false_positive_when_system_attempts_an_unrecoverable_case():
    # bank_decline diagnosis -> agent proposes ESCALATE_HUMAN by fallback rule,
    # so use insufficient_funds (proposes SCHEDULE_RETRY, an "attempt") with
    # ground truth saying it was NOT actually recoverable.
    engine = _engine()
    record = _record(
        failure_code="INSUFFICIENT_FUNDS",
        ground_truth={"category": "insufficient_funds", "is_recoverable": False,
                       "recommended_strategy": "schedule_retry", "dataset_split": "holdout"},
    )
    metrics = engine.evaluate([record])
    assert metrics.false_positive_count == 1
    assert metrics.false_positive_rate == 1.0
    assert metrics.false_positive_cost > 0


def test_false_negative_when_system_escalates_a_recoverable_case():
    engine = _engine()
    record = _record(
        failure_code="BANK_DECLINE",  # agent's fallback -> ESCALATE_HUMAN (not an "attempt")
        ground_truth={"category": "bank_decline", "is_recoverable": True,
                       "recommended_strategy": "escalate_or_stop", "dataset_split": "holdout"},
    )
    metrics = engine.evaluate([record])
    assert metrics.false_negative_rate == 1.0


def test_no_false_positive_when_unrecoverable_case_is_correctly_escalated():
    engine = _engine()
    record = _record(
        failure_code="BANK_DECLINE",
        ground_truth={"category": "bank_decline", "is_recoverable": False,
                       "recommended_strategy": "escalate_or_stop", "dataset_split": "holdout"},
    )
    metrics = engine.evaluate([record])
    assert metrics.false_positive_count == 0


# --- Diagnosis / action-selection accuracy -----------------------------------


def test_diagnosis_accuracy_perfect_when_all_categories_match():
    engine = _engine()
    records = [
        _record(
            event_id=f"evt_{i}", failure_code="NETWORK_ERROR",
            ground_truth={"category": "temporary_failure", "is_recoverable": True,
                           "recommended_strategy": "retry_later", "dataset_split": "holdout"},
        )
        for i in range(10)
    ]
    metrics = engine.evaluate(records)
    assert metrics.ai_diagnosis_accuracy == 1.0


def test_action_selection_accuracy_accepts_either_side_of_ambiguous_strategy():
    engine = _engine()
    # bank_decline's fallback maps to ESCALATE_HUMAN, which IS one of the two
    # acceptable actions for "escalate_or_stop" -- should count as correct.
    record = _record(
        failure_code="BANK_DECLINE",
        ground_truth={"category": "bank_decline", "is_recoverable": False,
                       "recommended_strategy": "escalate_or_stop", "dataset_split": "holdout"},
    )
    metrics = engine.evaluate([record])
    assert metrics.action_selection_accuracy == 1.0


# --- Cost model ---------------------------------------------------------------


def test_net_recovered_value_subtracts_false_positive_cost():
    engine = _engine()
    fp_record = _record(
        failure_code="INSUFFICIENT_FUNDS",
        ground_truth={"category": "insufficient_funds", "is_recoverable": False,
                       "recommended_strategy": "schedule_retry", "dataset_split": "holdout"},
    )
    metrics = engine.evaluate([fp_record])
    assert metrics.net_recovered_value == metrics.revenue_recovered - metrics.false_positive_cost


def test_false_positive_cost_zero_when_no_false_positives():
    engine = _engine()
    record = _record()  # temporary_failure, is_recoverable=True -- no FP possible here
    metrics = engine.evaluate([record])
    assert metrics.false_positive_count == 0
    assert metrics.false_positive_cost == 0
    assert metrics.net_recovered_value == metrics.revenue_recovered


# --- Human escalation rate & policy-blocked count ----------------------------


def test_human_escalation_rate_counted_over_full_batch_including_controls():
    engine = _engine()
    escalating = _record(
        event_id="evt_escalate", failure_code="BANK_DECLINE",
        ground_truth={"category": "bank_decline", "is_recoverable": False,
                       "recommended_strategy": "escalate_or_stop", "dataset_split": "holdout"},
    )
    non_escalating = _record(event_id="evt_normal")  # temporary_failure -> RETRY_PAYMENT
    metrics = engine.evaluate([escalating, non_escalating])
    assert metrics.human_escalation_rate == 0.5


def test_policy_blocked_actions_counts_non_allowed_decisions():
    engine = _engine()
    # High attempt count -> repeated_failure diagnosis -> agent proposes
    # ESCALATE_HUMAN itself, which policy then approves (not blocks). To force
    # an actual POLICY_BLOCKED we'd need a misbehaving proposal, which is
    # covered in test_orchestrator.py -- here we just confirm the count field
    # reflects `not decision.allowed` accurately for the default (0) case.
    record = _record()
    metrics = engine.evaluate([record])
    assert metrics.policy_blocked_actions == 0


# --- Determinism --------------------------------------------------------------


def test_same_seed_produces_identical_metrics():
    records = [_record(event_id=f"evt_{i}", payment_id=f"pay_{i}") for i in range(20)]
    metrics_a = _engine(seed=7).evaluate(records)
    metrics_b = _engine(seed=7).evaluate(records)
    assert metrics_a == metrics_b
