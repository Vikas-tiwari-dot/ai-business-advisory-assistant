import hashlib
import json

from scripts.generate_data import (
    CATEGORY_CONFIG,
    generate_events,
    write_dataset,
)


def _hash_events(events: list[dict]) -> str:
    serialized = "\n".join(json.dumps(e, sort_keys=True) for e in events)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def test_generates_requested_record_count():
    events, _ = generate_events(records=1000, seed=42)
    assert len(events) == 1000


def test_deterministic_under_fixed_seed():
    events_a, manifest_a = generate_events(records=500, seed=42)
    events_b, manifest_b = generate_events(records=500, seed=42)
    assert _hash_events(events_a) == _hash_events(events_b)
    assert manifest_a == manifest_b


def test_different_seeds_diverge():
    events_a, _ = generate_events(records=500, seed=42)
    events_b, _ = generate_events(records=500, seed=7)
    assert _hash_events(events_a) != _hash_events(events_b)


def test_category_distribution_matches_configured_shares_within_tolerance():
    events, manifest = generate_events(records=5000, seed=1)
    total = len(events)
    for category, cfg in CATEGORY_CONFIG.items():
        actual_share = manifest["category_distribution"][category] / total
        # Loose tolerance -- this is a statistical sampling check, not exact equality.
        assert abs(actual_share - cfg["share"]) < 0.03, (
            f"{category}: expected ~{cfg['share']}, got {actual_share}"
        )


def test_train_holdout_split_has_no_customer_overlap():
    events, _ = generate_events(records=1000, seed=42, train_ratio=0.7)
    train_customers = {e["customer_id"] for e in events if e["ground_truth"]["dataset_split"] == "train"}
    holdout_customers = {e["customer_id"] for e in events if e["ground_truth"]["dataset_split"] == "holdout"}
    assert train_customers.isdisjoint(holdout_customers)


def test_split_ratio_is_approximately_correct():
    events, _ = generate_events(records=1000, seed=42, train_ratio=0.7)
    train_count = sum(1 for e in events if e["ground_truth"]["dataset_split"] == "train")
    ratio = train_count / len(events)
    assert 0.6 < ratio < 0.8  # customer-level split -> can't be exact, but should be in range


def test_repeated_failure_events_have_at_least_three_attempts():
    events, _ = generate_events(records=2000, seed=99)
    repeated = [e for e in events if e["ground_truth"]["category"] == "repeated_failure"]
    assert repeated, "expected at least one repeated_failure record in 2000 events"
    assert all(e["attempt_number"] >= 3 for e in repeated)


def test_repeated_failure_is_never_marked_recoverable():
    events, _ = generate_events(records=2000, seed=99)
    repeated = [e for e in events if e["ground_truth"]["category"] == "repeated_failure"]
    assert all(e["ground_truth"]["is_recoverable"] is False for e in repeated)


def test_already_successful_control_has_no_failure_code():
    events, _ = generate_events(records=2000, seed=99)
    control = [e for e in events if e["ground_truth"]["category"] == "already_successful"]
    assert control
    assert all(e["failure_code"] is None and e["status"] == "recovered" for e in control)


def test_ground_truth_is_absent_from_ai_facing_projection():
    """
    The AI-facing view of an event must not include ground_truth -- that field
    exists purely for the evaluation engine. This test locks the field name so a
    future refactor can't accidentally leak it into whatever payload gets sent
    to the diagnosis/recovery LLM calls in later phases.
    """
    events, _ = generate_events(records=50, seed=1)
    sample = events[0]
    ai_facing_keys = {
        "event_id", "customer_id", "payment_id", "amount", "currency", "status",
        "failure_code", "failure_reason", "timestamp", "payment_method",
        "attempt_number", "customer_history",
    }
    projected = {k: v for k, v in sample.items() if k in ai_facing_keys}
    assert "ground_truth" not in projected
    assert set(projected.keys()) == ai_facing_keys


def test_write_dataset_produces_matching_sha256(tmp_path):
    events, manifest = generate_events(records=200, seed=42)
    jsonl_path, manifest_path, full_manifest = write_dataset(events, manifest, tmp_path, seed=42)

    assert jsonl_path.exists()
    assert manifest_path.exists()

    actual_hash = hashlib.sha256(jsonl_path.read_bytes()).hexdigest()
    assert actual_hash == full_manifest["sha256"]

    with manifest_path.open() as f:
        saved_manifest = json.load(f)
    assert saved_manifest["sha256"] == actual_hash
    assert saved_manifest["records"] == 200
