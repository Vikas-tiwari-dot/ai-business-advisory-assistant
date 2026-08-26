import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { Panel, LoadingState, ErrorState, EmptyState } from "../components/ui";
import StageChain from "../components/StageChain";

const STAGES = [
  "", "PAYMENT_FAILED", "RISK_DETECTED", "AI_DIAGNOSED", "AI_FALLBACK_USED", "ACTION_PROPOSED",
  "POLICY_APPROVED", "POLICY_BLOCKED", "ACTION_EXECUTED", "PAYMENT_RECOVERED", "ESCALATED",
  "HUMAN_DECISION", "DUPLICATE_IGNORED",
];

export default function Audit() {
  const [entries, setEntries] = useState(null);
  const [error, setError] = useState(null);
  const [stage, setStage] = useState("");

  useEffect(() => {
    setError(null);
    const params = { limit: 100 };
    if (stage) params.stage = stage;
    api.listAudit(params).then(setEntries).catch((e) => setError(e.message));
  }, [stage]);

  if (error) return <ErrorState message={error} />;

  return (
    <div className="space-y-6 p-8">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold text-mist-50">Audit Trail</h1>
          <p className="mt-0.5 text-xs text-mist-400">Every decision, append-only, across the whole system.</p>
        </div>
        <select
          value={stage}
          onChange={(e) => setStage(e.target.value)}
          className="rounded-lg border border-ink-600 bg-ink-800 px-3 py-1.5 text-xs text-mist-100 outline-none focus:border-recovered"
        >
          {STAGES.map((s) => (
            <option key={s} value={s}>
              {s === "" ? "All stages" : s.replaceAll("_", " ")}
            </option>
          ))}
        </select>
      </div>

      <Panel>
        {!entries ? (
          <LoadingState label="Loading audit trail…" />
        ) : entries.length === 0 ? (
          <EmptyState title="No audit entries" description="Generate payments from the Overview page first." />
        ) : (
          <div className="divide-y divide-ink-700">
            {entries.map((e) => (
              <div key={e.id} className="px-5 py-3.5">
                <div className="mb-2 flex items-center justify-between">
                  <StageChain entries={[e]} />
                  <time className="font-mono text-2xs text-mist-400">{new Date(e.timestamp).toLocaleString()}</time>
                </div>
                {Object.keys(e.payload || {}).length > 0 && (
                  <pre className="mt-1 max-w-full overflow-x-auto rounded-md bg-ink-900 px-2.5 py-2 font-mono text-2xs text-mist-300">
                    {JSON.stringify(e.payload, null, 2)}
                  </pre>
                )}
              </div>
            ))}
          </div>
        )}
      </Panel>
    </div>
  );
}
