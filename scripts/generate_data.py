"""
Synthetic payment event generator for RazorRecover AI.

Produces normalized payment-failure (and control) events matching the schema in
docs/architecture.md §5, deterministic under a fixed --seed. No network calls,
no dependency on the backend app -- this is pure data generation so it can run
standalone before the database/API even exist (Phase 3 of the build).

Usage:
    python scripts/generate_data.py --records 1000 --seed 42
    python scripts/generate_data.py --records 1000 --seed 42 --output data/synthetic

Output:
    <output>/payments_seed<seed>.jsonl   one normalized event per line
    <output>/manifest_seed<seed>.json    seed, category distribution, split ratio,
                                         sha256 of the jsonl file (for determinism checks)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Category configuration -- mirrors docs/architecture.md §5 exactly.
# `share` must sum to 1.0. `recover_prob` is the ground-truth probability that
# this case is genuinely recoverable (used later by the RecoverySimulator in
# Phase 6/8 and by the EvaluationEngine in Phase 10 to score false pos/neg).
# ---------------------------------------------------------------------------
CATEGORY_CONFIG: dict[str, dict] = {
    "temporary_failure": {
        "share": 0.20,
        "recover_prob": 0.75,
        "failure_codes": ["NETWORK_ERROR", "GATEWAY_TIMEOUT"],
        "max_attempt": 2,
        "recommended_strategy": "retry_later",
    },
    "insufficient_funds": {
        "share": 0.15,
        "recover_prob": 0.30,
        "failure_codes": ["INSUFFICIENT_FUNDS"],
        "max_attempt": 2,
        "recommended_strategy": "schedule_retry",
    },
    "bank_decline": {
        "share": 0.15,
        "recover_prob": 0.10,
        "failure_codes": ["BANK_DECLINE", "ISSUER_DECLINE"],
        "max_attempt": 3,
        "recommended_strategy": "escalate_or_stop",
    },
    "expired_instrument": {
        "share": 0.10,
        "recover_prob": 0.55,  # recoverable, but only via OFFER_ALTERNATE_METHOD
        "failure_codes": ["CARD_EXPIRED", "INSTRUMENT_INVALID"],
        "max_attempt": 1,
        "recommended_strategy": "offer_alternate_method",
    },
    "repeated_failure": {
        "share": 0.10,
        "recover_prob": 0.05,  # policy forces ESCALATE_HUMAN regardless
        "failure_codes": ["BANK_DECLINE", "INSUFFICIENT_FUNDS", "NETWORK_ERROR"],
        "max_attempt": 4,  # 3+ attempts by definition
        "min_attempt": 3,
        "recommended_strategy": "escalate_human",
    },
    "checkout_abandonment": {
        "share": 0.15,
        "recover_prob": 0.40,
        "failure_codes": ["CHECKOUT_ABANDONED"],
        "max_attempt": 1,
        "recommended_strategy": "send_reminder",
    },
    "overdue_invoice": {
        "share": 0.10,
        "recover_prob": 0.50,
        "failure_codes": ["INVOICE_OVERDUE"],
        "max_attempt": 1,
        "recommended_strategy": "send_reminder",
    },
    "already_successful": {
        "share": 0.05,
        "recover_prob": None,  # not applicable -- control case, nothing to recover
        "failure_codes": [],
        "max_attempt": 1,
        "recommended_strategy": "stop",
    },
}

PAYMENT_METHODS = ["card", "upi", "netbanking", "wallet", "emi"]
# Weighted toward card/UPI, matching typical Indian payment-mix.
PAYMENT_METHOD_WEIGHTS = [0.40, 0.35, 0.12, 0.08, 0.05]

CUSTOMER_SEGMENTS = ["new", "standard", "high_value"]
CUSTOMER_SEGMENT_WEIGHTS = [0.30, 0.50, 0.20]

# Amount ranges (minor units, i.e. paise) per segment -- high value customers
# transact bigger tickets on average.
SEGMENT_AMOUNT_RANGE = {
    "new": (19900, 299900),        # ₹199 - ₹2,999
    "standard": (49900, 999900),   # ₹499 - ₹9,999
    "high_value": (499900, 4999900),  # ₹4,999 - ₹49,999
}

NOW = datetime(2026, 8, 22, tzinfo=timezone.utc)  # anchor date for reproducibility


@dataclass
class CustomerProfile:
    customer_id: str
    segment: str
    previous_successful_payments: int
    previous_failed_payments: int
    lifetime_value: int  # minor units
    last_successful_payment_at: str | None
    opted_out: bool = False


def _weighted_choice(rng: random.Random, options: list[str], weights: list[float]) -> str:
    return rng.choices(options, weights=weights, k=1)[0]


def _build_customer_pool(rng: random.Random, size: int) -> list[CustomerProfile]:
    pool: list[CustomerProfile] = []
    for i in range(size):
        segment = _weighted_choice(rng, CUSTOMER_SEGMENTS, CUSTOMER_SEGMENT_WEIGHTS)
        if segment == "new":
            prev_success = rng.randint(0, 2)
            prev_failed = rng.randint(0, 1)
        elif segment == "standard":
            prev_success = rng.randint(3, 20)
            prev_failed = rng.randint(0, 4)
        else:  # high_value
            prev_success = rng.randint(15, 80)
            prev_failed = rng.randint(0, 5)

        lo, hi = SEGMENT_AMOUNT_RANGE[segment]
        avg_amount = rng.randint(lo, hi)
        lifetime_value = avg_amount * max(prev_success, 1)

        last_success_days_ago = rng.randint(1, 120) if prev_success > 0 else None
        last_success_at = (
            (NOW - timedelta(days=last_success_days_ago)).isoformat()
            if last_success_days_ago is not None
            else None
        )

        # ~3% opt-out rate, used by the policy engine's "never contact after opt-out" rule.
        opted_out = rng.random() < 0.03

        pool.append(
            CustomerProfile(
                customer_id=f"cust_{i:05d}",
                segment=segment,
                previous_successful_payments=prev_success,
                previous_failed_payments=prev_failed,
                lifetime_value=lifetime_value,
                last_successful_payment_at=last_success_at,
                opted_out=opted_out,
            )
        )
    return pool


def _split_for_customer(customer_id: str, train_ratio: float) -> str:
    """
    Deterministic hash-based split so a given customer always lands in the same
    split regardless of generation order, and so the split is reproducible
    without being stored per-customer. Splitting by customer (not by event)
    prevents the same customer's history leaking across train/holdout.
    """
    digest = hashlib.sha256(customer_id.encode("utf-8")).hexdigest()
    bucket = int(digest[:8], 16) / 0xFFFFFFFF  # -> [0, 1)
    return "train" if bucket < train_ratio else "holdout"


def _category_sequence(rng: random.Random, records: int) -> list[str]:
    """Deterministic list of categories in generation order, matching configured shares."""
    categories = list(CATEGORY_CONFIG.keys())
    weights = [CATEGORY_CONFIG[c]["share"] for c in categories]
    assert abs(sum(weights) - 1.0) < 1e-6, "CATEGORY_CONFIG shares must sum to 1.0"
    return rng.choices(categories, weights=weights, k=records)


def generate_events(records: int, seed: int, train_ratio: float = 0.7) -> tuple[list[dict], dict]:
    """
    Returns (events, manifest_stats). Pure function of (records, seed, train_ratio) --
    same inputs always produce byte-identical output, which is what the
    determinism test checks via sha256 of the serialized file.
    """
    rng = random.Random(seed)

    customer_pool_size = max(50, records // 5)
    customers = _build_customer_pool(rng, customer_pool_size)

    categories = _category_sequence(rng, records)

    events: list[dict] = []
    category_counts: dict[str, int] = {c: 0 for c in CATEGORY_CONFIG}
    split_counts = {"train": 0, "holdout": 0}

    for i in range(records):
        category = categories[i]
        cfg = CATEGORY_CONFIG[category]
        category_counts[category] += 1

        customer = rng.choice(customers)
        split = _split_for_customer(customer.customer_id, train_ratio)
        split_counts[split] += 1

        lo, hi = SEGMENT_AMOUNT_RANGE[customer.segment]
        amount = rng.randint(lo, hi)

        payment_method = _weighted_choice(rng, PAYMENT_METHODS, PAYMENT_METHOD_WEIGHTS)

        min_attempt = cfg.get("min_attempt", 1)
        attempt_number = rng.randint(min_attempt, cfg["max_attempt"])

        days_ago = rng.randint(0, 89)
        seconds_offset = rng.randint(0, 86399)
        timestamp = NOW - timedelta(days=days_ago, seconds=-seconds_offset)

        event_id = f"evt_{i:06d}"
        payment_id = f"pay_{i:06d}"

        if category == "already_successful":
            status = "recovered"
            failure_code = None
            failure_reason = None
            is_recoverable = None  # not applicable -- there is nothing to recover
        else:
            status = "failed"
            failure_code = rng.choice(cfg["failure_codes"])
            failure_reason = _failure_reason_text(failure_code)
            recover_prob = cfg["recover_prob"]
            is_recoverable = (
                rng.random() < recover_prob if category != "repeated_failure" else False
            )

        event = {
            "event_id": event_id,
            "customer_id": customer.customer_id,
            "payment_id": payment_id,
            "amount": amount,
            "currency": "INR",
            "status": status,
            "failure_code": failure_code,
            "failure_reason": failure_reason,
            "timestamp": timestamp.isoformat(),
            "payment_method": payment_method,
            "attempt_number": attempt_number,
            "customer_history": {
                "previous_successful_payments": customer.previous_successful_payments,
                "previous_failed_payments": customer.previous_failed_payments,
                "lifetime_value": customer.lifetime_value,
                "last_successful_payment_at": customer.last_successful_payment_at,
                "opted_out": customer.opted_out,
            },
            "ground_truth": {
                "category": category,
                "is_recoverable": is_recoverable,
                "recommended_strategy": cfg["recommended_strategy"],
                "dataset_split": split,
            },
        }
        events.append(event)

    # Sort by timestamp for a realistic chronological event stream, but keep
    # event_id/payment_id assignment stable (assigned before sort) so IDs don't
    # depend on the sort -- another determinism guard.
    events.sort(key=lambda e: e["timestamp"])

    manifest = {
        "seed": seed,
        "records": records,
        "train_ratio": train_ratio,
        "customer_pool_size": customer_pool_size,
        "category_distribution": category_counts,
        "split_distribution": split_counts,
        "generated_at_anchor": NOW.isoformat(),
    }
    return events, manifest


def _failure_reason_text(failure_code: str) -> str:
    reasons = {
        "NETWORK_ERROR": "Temporary network error between gateway and issuing bank",
        "GATEWAY_TIMEOUT": "Payment gateway timed out before confirmation",
        "INSUFFICIENT_FUNDS": "Insufficient balance in customer account",
        "BANK_DECLINE": "Issuing bank declined the transaction",
        "ISSUER_DECLINE": "Card issuer declined the transaction",
        "CARD_EXPIRED": "Card has expired",
        "INSTRUMENT_INVALID": "Payment instrument is no longer valid",
        "CHECKOUT_ABANDONED": "Customer did not complete checkout",
        "INVOICE_OVERDUE": "Invoice due date has passed without payment",
    }
    return reasons.get(failure_code, "Unspecified failure")


def write_dataset(events: list[dict], manifest: dict, output_dir: Path, seed: int) -> tuple[Path, Path, dict]:
    output_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = output_dir / f"payments_seed{seed}.jsonl"
    manifest_path = output_dir / f"manifest_seed{seed}.json"

    with jsonl_path.open("w", encoding="utf-8") as f:
        for event in events:
            f.write(json.dumps(event, sort_keys=True) + "\n")

    sha256 = hashlib.sha256(jsonl_path.read_bytes()).hexdigest()
    full_manifest = {**manifest, "sha256": sha256, "file": jsonl_path.name}

    with manifest_path.open("w", encoding="utf-8") as f:
        json.dump(full_manifest, f, indent=2, sort_keys=True)

    return jsonl_path, manifest_path, full_manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate synthetic RazorRecover payment events.")
    parser.add_argument("--records", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--train-ratio", type=float, default=0.7)
    parser.add_argument("--output", type=str, default="data/synthetic")
    args = parser.parse_args()

    events, manifest = generate_events(args.records, args.seed, args.train_ratio)
    jsonl_path, manifest_path, full_manifest = write_dataset(events, manifest, Path(args.output), args.seed)

    print(f"Wrote {len(events)} events -> {jsonl_path}")
    print(f"Manifest -> {manifest_path}")
    print(f"Category distribution: {full_manifest['category_distribution']}")
    print(f"Split distribution: {full_manifest['split_distribution']}")
    print(f"sha256: {full_manifest['sha256']}")


if __name__ == "__main__":
    main()
