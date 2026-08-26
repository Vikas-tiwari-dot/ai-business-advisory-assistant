"""
Run batch evaluation against the synthetic dataset's holdout split.

Usage:
    python scripts/run_evaluation.py --input data/synthetic/payments_seed42.jsonl
    python scripts/run_evaluation.py --input data/synthetic/payments_seed42.jsonl --seed 7 --output data/evaluation

Uses AI_PROVIDER from the environment (defaults to "none" -> deterministic
fallback rules, so this runs with zero external credentials). Writes a JSON
report to --output and prints a human-readable summary to stdout.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.config import get_settings  # noqa: E402
from app.services.ai.registry import get_provider  # noqa: E402
from app.services.diagnosis.engine import DiagnosisEngine  # noqa: E402
from app.services.evaluation.engine import EvaluationEngine  # noqa: E402
from app.services.payment_gateway.simulator import RecoverySimulator  # noqa: E402
from app.services.recovery_agent.agent import RecoveryAgent  # noqa: E402


def load_records(path: Path) -> list[dict]:
    records = []
    with path.open() as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def _persist_to_db(metrics, *, run_id: str) -> None:
    """
    Writes an EvaluationResult row so GET /api/evaluation has something real
    to serve. This is a distinct concept from the live /api/metrics endpoint,
    which has no ground truth to compare against -- see app/api/evaluation.py.
    """
    from app.db.session import SessionLocal
    from app.models.audit import EvaluationResult

    db = SessionLocal()
    try:
        db.add(EvaluationResult(run_id=run_id, dataset_split="holdout", metrics_json=metrics.model_dump()))
        db.commit()
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run RazorRecover AI batch evaluation.")
    parser.add_argument("--input", type=str, required=True, help="Path to a payments_seed*.jsonl file")
    parser.add_argument("--seed", type=int, default=42, help="RNG seed for the recovery simulator")
    parser.add_argument("--output", type=str, default="data/evaluation", help="Directory to write the JSON report")
    args = parser.parse_args()

    settings = get_settings()
    records = load_records(Path(args.input))

    provider = get_provider(settings)
    diagnosis_engine = DiagnosisEngine(provider=provider, timeout=settings.ai_timeout_seconds)
    recovery_agent = RecoveryAgent(provider=provider, timeout=settings.ai_timeout_seconds)
    gateway = RecoverySimulator(rng=random.Random(args.seed))

    engine = EvaluationEngine(diagnosis_engine, recovery_agent, gateway)
    metrics = engine.evaluate(records, dataset_split="holdout")

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / f"evaluation_seed{args.seed}.json"
    report_path.write_text(json.dumps(metrics.model_dump(), indent=2, sort_keys=True))

    _persist_to_db(metrics, run_id=f"seed{args.seed}")

    print(f"AI provider: {settings.ai_provider} ({'live' if provider else 'deterministic fallback'})")
    print(f"Evaluated {metrics.total_records} holdout records ({metrics.scored_record_count} scored, excl. controls)")
    print()
    print(f"Total revenue:              ₹{metrics.total_revenue / 100:,.0f}")
    print(f"Revenue at risk:            ₹{metrics.revenue_at_risk / 100:,.0f}")
    print(f"Recoverable cases:          {metrics.recoverable_cases}")
    print(f"Successful recoveries:      {metrics.successful_recoveries}")
    print(f"Revenue recovered:          ₹{metrics.revenue_recovered / 100:,.0f}")
    print(f"Recovery rate:              {metrics.recovery_rate:.1%}")
    print(f"Average recovery value:     ₹{metrics.average_recovery_value / 100:,.0f}")
    print()
    print(f"False positive rate:        {metrics.false_positive_rate:.1%}")
    print(f"False negative rate:        {metrics.false_negative_rate:.1%}")
    print(f"AI diagnosis accuracy:      {metrics.ai_diagnosis_accuracy:.1%}")
    print(f"Action selection accuracy:  {metrics.action_selection_accuracy:.1%}")
    print()
    print(f"Human escalation rate:      {metrics.human_escalation_rate:.1%}")
    print(f"Policy-blocked actions:     {metrics.policy_blocked_actions}")
    print()
    print(f"False positive count:       {metrics.false_positive_count}")
    print(f"False positive cost:        ₹{metrics.false_positive_cost / 100:,.0f}")
    print(f"Net recovered value:        ₹{metrics.net_recovered_value / 100:,.0f}")
    print()
    print(f"Report written to {report_path}")


if __name__ == "__main__":
    main()
