import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { Panel, PanelHeader, StatCard, LoadingState, ErrorState } from "../components/ui";

export default function Evaluation() {
  const [evalData, setEvalData] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    api.getEvaluation().then(setEvalData).catch((e) => setError(e.message));
  }, []);

  if (error) return <ErrorState message={error} />;
  if (!evalData) return <LoadingState label="Loading evaluation…" />;

  if (!evalData.available) {
    return (
      <div className="space-y-6 p-8">
        <h1 className="text-lg font-semibold text-mist-50">Evaluation</h1>
        <Panel className="px-6 py-10 text-center">
          <p className="text-sm font-medium text-mist-100">No evaluation report yet</p>
          <p className="mx-auto mt-2 max-w-md text-xs text-mist-400">
            Run <code className="rounded bg-ink-900 px-1.5 py-0.5 font-mono text-2xs">python scripts/run_evaluation.py --input data/synthetic/payments_seed42.jsonl</code>{" "}
            to score the pipeline against the held-out split with ground truth. This is intentionally separate from
            the live Overview metrics, which have no ground truth to compare against.
          </p>
        </Panel>
      </div>
    );
  }

  const m = evalData.metrics;

  return (
    <div className="space-y-6 p-8">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold text-mist-50">Evaluation</h1>
          <p className="mt-0.5 text-xs text-mist-400">
            Scored against the <span className="text-mist-100">{evalData.dataset_split}</span> split only — never
            the data used to tune any rule. Run <span className="font-mono text-mist-300">{evalData.run_id}</span> ·{" "}
            {new Date(evalData.created_at).toLocaleString()}
          </p>
        </div>
      </div>

      <div className="grid grid-cols-4 gap-4">
        <StatCard label="Total records" value={m.total_records} mono={false} />
        <StatCard label="Scored (excl. controls)" value={m.scored_record_count} mono={false} />
        <StatCard label="Recoverable cases" value={m.recoverable_cases} mono={false} />
        <StatCard label="Successful recoveries" value={m.successful_recoveries} mono={false} tone="recovered" />
      </div>

      <div className="grid grid-cols-3 gap-4">
        <StatCard label="Revenue recovered" value={`₹${(m.revenue_recovered / 100).toLocaleString()}`} tone="recovered" />
        <StatCard label="Recovery rate" value={`${(m.recovery_rate * 100).toFixed(1)}%`} tone="recovered" />
        <StatCard label="Avg recovery value" value={`₹${(m.average_recovery_value / 100).toLocaleString()}`} />
      </div>

      <Panel>
        <PanelHeader title="Classification quality" subtitle="Scored only on non-control records" />
        <div className="grid grid-cols-4 divide-x divide-ink-700">
          {[
            ["AI diagnosis accuracy", m.ai_diagnosis_accuracy],
            ["Action selection accuracy", m.action_selection_accuracy],
            ["False positive rate", m.false_positive_rate],
            ["False negative rate", m.false_negative_rate],
          ].map(([label, value]) => (
            <div key={label} className="px-5 py-4">
              <p className="text-2xs uppercase tracking-wide text-mist-400">{label}</p>
              <p className="mt-1.5 font-mono text-xl font-semibold text-mist-50">{(value * 100).toFixed(1)}%</p>
            </div>
          ))}
        </div>
      </Panel>

      <Panel>
        <PanelHeader title="Safety behavior" subtitle="How often the system stayed cautious" />
        <div className="grid grid-cols-2 divide-x divide-ink-700">
          <div className="px-5 py-4">
            <p className="text-2xs uppercase tracking-wide text-mist-400">Human escalation rate</p>
            <p className="mt-1.5 font-mono text-xl font-semibold text-risk">{(m.human_escalation_rate * 100).toFixed(1)}%</p>
          </div>
          <div className="px-5 py-4">
            <p className="text-2xs uppercase tracking-wide text-mist-400">Policy-blocked actions</p>
            <p className="mt-1.5 font-mono text-xl font-semibold text-blocked">{m.policy_blocked_actions}</p>
          </div>
        </div>
      </Panel>

      <Panel>
        <PanelHeader title="False-positive cost model" subtitle="Estimated intervention cost vs. net value" />
        <div className="grid grid-cols-3 divide-x divide-ink-700">
          <div className="px-5 py-4">
            <p className="text-2xs uppercase tracking-wide text-mist-400">False positive count</p>
            <p className="mt-1.5 font-mono text-xl font-semibold text-mist-50">{m.false_positive_count}</p>
          </div>
          <div className="px-5 py-4">
            <p className="text-2xs uppercase tracking-wide text-mist-400">False positive cost</p>
            <p className="mt-1.5 font-mono text-xl font-semibold text-blocked">
              ₹{(m.false_positive_cost / 100).toLocaleString()}
            </p>
          </div>
          <div className="px-5 py-4">
            <p className="text-2xs uppercase tracking-wide text-mist-400">Net recovered value</p>
            <p className="mt-1.5 font-mono text-xl font-semibold text-recovered">
              ₹{(m.net_recovered_value / 100).toLocaleString()}
            </p>
          </div>
        </div>
      </Panel>
    </div>
  );
}
