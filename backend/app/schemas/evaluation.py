from pydantic import BaseModel, Field


class EvaluationMetrics(BaseModel):
    """
    The 15 metrics required by spec §7, plus the false-positive cost model
    from spec §8. All computed against the holdout split only -- see
    app/services/evaluation/engine.py's module docstring for how that's
    enforced.
    """

    # -- Batch composition --
    total_records: int
    total_revenue: int = Field(description="Minor units, sum of amount across every evaluated record")

    # -- Risk / recovery numbers --
    revenue_at_risk: int = Field(description="Minor units")
    recoverable_cases: int = Field(description="Records where ground-truth is_recoverable=True (excludes controls)")
    successful_recoveries: int = Field(description="Records where the pipeline's own execution actually succeeded")
    revenue_recovered: int = Field(description="Minor units, sum of actual recovered revenue")
    recovery_rate: float = Field(description="revenue_recovered / revenue_at_risk, 0 if no risk")
    average_recovery_value: int = Field(description="Minor units, revenue_recovered / successful_recoveries, 0 if none")

    # -- Classification quality (scored only on non-control records) --
    false_positive_rate: float = Field(
        description="Attempted recovery on a case that ground truth says was NOT recoverable, "
        "as a share of all not-recoverable cases"
    )
    false_negative_rate: float = Field(
        description="Did not attempt recovery on a case ground truth says WAS recoverable, "
        "as a share of all recoverable cases"
    )
    ai_diagnosis_accuracy: float = Field(description="Diagnosis category matches ground-truth category")
    action_selection_accuracy: float = Field(
        description="Final (post-policy) action matches the action implied by ground-truth's recommended strategy"
    )

    # -- Safety / policy behavior --
    human_escalation_rate: float = Field(description="Share of all evaluated records that ended in ESCALATE_HUMAN")
    policy_blocked_actions: int = Field(description="Count of records where the policy engine did not allow the proposed action as-is")

    # -- Cost model (spec §8) --
    false_positive_count: int
    false_positive_cost: int = Field(description="Minor units, false_positive_count * configured unit cost")
    net_recovered_value: int = Field(description="Minor units, revenue_recovered - false_positive_cost")

    # -- Provenance --
    dataset_split: str
    scored_record_count: int = Field(description="Non-control records used for accuracy/FP/FN metrics")
