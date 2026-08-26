"""
Batch Evaluation Engine (spec §7-8).

Computes the 15 required metrics plus the false-positive cost model, run
ONLY against the holdout split of the synthetic dataset -- never the split
used to tune anything in this codebase (spec: "Do NOT evaluate on the same
records used to tune the rules"). Enforced two ways:

  1. The public entrypoint's `dataset_split` parameter only accepts
     "holdout" and raises ValueError on anything else.
  2. Even when handed a raw batch that happens to include train rows, every
     metric filters to `ground_truth.dataset_split == "holdout"` before
     scoring anything -- a caller can't accidentally leak train rows into
     the numbers just by forgetting to pre-filter.

Ground truth (`ground_truth.category`, `ground_truth.is_recoverable`) is used
ONLY in this module, to score the pipeline's real outputs after the fact. It
is never passed into any AI or business-logic service upstream of this --
see scripts/generate_data.py's own test locking that boundary.

Diagnosis/action-selection accuracy and the false-positive/negative rates
exclude the "already_successful" control category, since those records exist
to test the "never touch an already-recovered payment" safety rule, not to
be diagnosed -- there is no diagnosis category that correctly describes "this
was never actually at risk," so scoring them as diagnosis errors would be
measuring the wrong thing.
"""
from typing import Any

from app.core.config import get_settings
from app.schemas.events import CustomerHistory, NormalizedEvent
from app.schemas.evaluation import EvaluationMetrics
from app.services.diagnosis.context_engine import compute_customer_context
from app.services.diagnosis.engine import DiagnosisEngine
from app.services.payment_gateway.gateway import NON_EXECUTING_ACTIONS, PaymentGateway
from app.services.policy_engine.engine import evaluate as evaluate_policy
from app.services.recovery_agent.agent import RecoveryAgent
from app.services.risk_detector.detector import assess_risk

ALLOWED_SPLIT = "holdout"
CONTROL_CATEGORY = "already_successful"

AI_FACING_KEYS = {
    "event_id", "customer_id", "payment_id", "amount", "currency", "status",
    "failure_code", "failure_reason", "timestamp", "payment_method",
    "attempt_number", "customer_history",
}

# Maps the synthetic generator's ground-truth `recommended_strategy` label
# (a loose narrative hint, not a bounded action) to the set of final actions
# that would count as "the system chose correctly." Some strategies map to
# more than one acceptable action -- e.g. bank_decline's "escalate_or_stop"
# is deliberately ambiguous between the two safe outcomes.
STRATEGY_TO_ACCEPTABLE_ACTIONS: dict[str, set[str]] = {
    "retry_later": {"RETRY_PAYMENT"},
    "schedule_retry": {"SCHEDULE_RETRY"},
    "escalate_or_stop": {"ESCALATE_HUMAN", "STOP"},
    "offer_alternate_method": {"OFFER_ALTERNATE_METHOD"},
    "escalate_human": {"ESCALATE_HUMAN"},
    "send_reminder": {"SEND_REMINDER"},
    "stop": {"STOP"},
}


def _to_normalized_event(raw: dict) -> NormalizedEvent:
    view = {k: v for k, v in raw.items() if k in AI_FACING_KEYS}
    if "customer_history" in view and not isinstance(view["customer_history"], CustomerHistory):
        view["customer_history"] = CustomerHistory(**view["customer_history"])
    return NormalizedEvent(**view)


class EvaluationEngine:
    def __init__(
        self,
        diagnosis_engine: DiagnosisEngine,
        recovery_agent: RecoveryAgent,
        gateway: PaymentGateway,
    ):
        self.diagnosis_engine = diagnosis_engine
        self.recovery_agent = recovery_agent
        self.gateway = gateway

    def _process_record(self, raw: dict) -> dict[str, Any]:
        event = _to_normalized_event(raw)
        risk = assess_risk(event)
        context = compute_customer_context(event.customer_history, recovery_attempts=0)
        diagnosis = self.diagnosis_engine.diagnose(event, context, risk)
        proposal = self.recovery_agent.propose(event, context, diagnosis, risk)
        decision = evaluate_policy(
            proposal, event, context, diagnosis, risk,
            payment_already_recovered=(event.status == "recovered"),
        )

        exec_result = None
        if decision.final_action not in NON_EXECUTING_ACTIONS:
            exec_result = self.gateway.execute(decision.final_action, event, diagnosis, risk.revenue_at_risk)

        return {
            "raw": raw,
            "event": event,
            "risk": risk,
            "diagnosis": diagnosis,
            "proposal": proposal,
            "decision": decision,
            "exec_result": exec_result,
        }

    def evaluate(self, raw_records: list[dict], *, dataset_split: str = "holdout") -> EvaluationMetrics:
        if dataset_split != ALLOWED_SPLIT:
            raise ValueError(
                f"EvaluationEngine only evaluates the {ALLOWED_SPLIT!r} split; "
                f"got {dataset_split!r}. Never evaluate on training data."
            )

        holdout_records = [
            r for r in raw_records
            if r.get("ground_truth", {}).get("dataset_split") == ALLOWED_SPLIT
        ]
        if not holdout_records:
            raise ValueError(
                "No holdout records found to evaluate -- refusing to run on an "
                "empty or train-only dataset."
            )

        results = [self._process_record(r) for r in holdout_records]
        return self._aggregate(results)

    def _aggregate(self, results: list[dict[str, Any]]) -> EvaluationMetrics:
        settings = get_settings()

        total_records = len(results)
        total_revenue = sum(r["event"].amount for r in results)
        revenue_at_risk = sum(r["risk"].revenue_at_risk for r in results)

        successful = [r for r in results if r["exec_result"] and r["exec_result"].result == "success"]
        revenue_recovered = sum(r["exec_result"].revenue_recovered for r in successful)
        recovery_rate = (revenue_recovered / revenue_at_risk) if revenue_at_risk > 0 else 0.0
        average_recovery_value = (revenue_recovered // len(successful)) if successful else 0

        human_escalation_rate = (
            sum(1 for r in results if r["decision"].final_action == "ESCALATE_HUMAN") / total_records
        )
        policy_blocked_actions = sum(1 for r in results if not r["decision"].allowed)

        # -- Non-control subset for classification metrics --
        scored = [r for r in results if r["raw"]["ground_truth"]["category"] != CONTROL_CATEGORY]
        scored_record_count = len(scored)

        recoverable = [r for r in scored if r["raw"]["ground_truth"]["is_recoverable"] is True]
        not_recoverable = [r for r in scored if r["raw"]["ground_truth"]["is_recoverable"] is False]

        def _attempted(r: dict[str, Any]) -> bool:
            return r["decision"].final_action not in NON_EXECUTING_ACTIONS and r["decision"].final_action != "IGNORE"

        false_positive_count = sum(1 for r in not_recoverable if _attempted(r))
        false_negative_count = sum(1 for r in recoverable if not _attempted(r))

        false_positive_rate = (false_positive_count / len(not_recoverable)) if not_recoverable else 0.0
        false_negative_rate = (false_negative_count / len(recoverable)) if recoverable else 0.0

        diagnosis_correct = sum(
            1 for r in scored if r["diagnosis"].diagnosis == r["raw"]["ground_truth"]["category"]
        )
        ai_diagnosis_accuracy = (diagnosis_correct / scored_record_count) if scored_record_count else 0.0

        action_correct = 0
        for r in scored:
            strategy = r["raw"]["ground_truth"].get("recommended_strategy")
            acceptable = STRATEGY_TO_ACCEPTABLE_ACTIONS.get(strategy, set())
            if r["decision"].final_action in acceptable:
                action_correct += 1
        action_selection_accuracy = (action_correct / scored_record_count) if scored_record_count else 0.0

        false_positive_cost = false_positive_count * settings.false_positive_unit_cost
        net_recovered_value = revenue_recovered - false_positive_cost

        return EvaluationMetrics(
            total_records=total_records,
            total_revenue=total_revenue,
            revenue_at_risk=revenue_at_risk,
            recoverable_cases=len(recoverable),
            successful_recoveries=len(successful),
            revenue_recovered=revenue_recovered,
            recovery_rate=round(recovery_rate, 4),
            average_recovery_value=average_recovery_value,
            false_positive_rate=round(false_positive_rate, 4),
            false_negative_rate=round(false_negative_rate, 4),
            ai_diagnosis_accuracy=round(ai_diagnosis_accuracy, 4),
            action_selection_accuracy=round(action_selection_accuracy, 4),
            human_escalation_rate=round(human_escalation_rate, 4),
            policy_blocked_actions=policy_blocked_actions,
            false_positive_count=false_positive_count,
            false_positive_cost=false_positive_cost,
            net_recovered_value=net_recovered_value,
            dataset_split=ALLOWED_SPLIT,
            scored_record_count=scored_record_count,
        )
