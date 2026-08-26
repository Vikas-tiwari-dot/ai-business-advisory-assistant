import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../lib/api";
import { Panel, StatusPill, LoadingState, ErrorState, EmptyState } from "../components/ui";

export default function Decisions() {
  const [payments, setPayments] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    api
      .listPayments({ page_size: 50 })
      .then((res) => setPayments(res.items))
      .catch((e) => setError(e.message));
  }, []);

  if (error) return <ErrorState message={error} />;
  if (!payments) return <LoadingState label="Loading AI decisions…" />;

  return (
    <div className="space-y-6 p-8">
      <div>
        <h1 className="text-lg font-semibold text-mist-50">AI Decisions</h1>
        <p className="mt-0.5 text-xs text-mist-400">
          Every diagnosis and action proposal is structured, validated, and falls back to deterministic rules if the
          AI output doesn't validate. Click any payment for the full reasoning trail.
        </p>
      </div>

      <Panel>
        {payments.length === 0 ? (
          <EmptyState title="No decisions yet" description="Generate payments from the Overview page first." />
        ) : (
          <div className="divide-y divide-ink-700">
            {payments.map((p) => (
              <Link
                key={p.id}
                to={`/payments/${p.id}`}
                className="flex items-center justify-between px-5 py-3.5 transition hover:bg-ink-700/40"
              >
                <div className="flex items-center gap-3">
                  <span className="font-mono text-xs text-signal">{p.id.slice(0, 8)}…</span>
                  <span className="text-2xs text-mist-400">{p.customer_external_id}</span>
                </div>
                <div className="flex items-center gap-3">
                  <span className="font-mono text-xs text-mist-100">{p.amount_display}</span>
                  <span className="font-mono text-2xs text-mist-400">
                    risk {p.risk_score != null ? p.risk_score.toFixed(2) : "—"}
                  </span>
                  {p.case_status && <StatusPill status={p.case_status} />}
                </div>
              </Link>
            ))}
          </div>
        )}
      </Panel>
    </div>
  );
}
