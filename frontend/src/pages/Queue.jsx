import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../lib/api";
import { Panel, Button, LoadingState, ErrorState, EmptyState } from "../components/ui";

export default function Queue() {
  const [items, setItems] = useState(null);
  const [error, setError] = useState(null);
  const [actingOn, setActingOn] = useState(null);

  const load = () => {
    setError(null);
    api.getQueue().then(setItems).catch((e) => setError(e.message));
  };

  useEffect(load, []);

  const act = async (paymentId, action) => {
    setActingOn(paymentId);
    try {
      await api[action](paymentId);
      load();
    } catch (e) {
      setError(e.message);
    } finally {
      setActingOn(null);
    }
  };

  if (error) return <ErrorState message={error} />;
  if (!items) return <LoadingState label="Loading queue…" />;

  return (
    <div className="space-y-6 p-8">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold text-mist-50">Recovery Queue</h1>
          <p className="mt-0.5 text-xs text-mist-400">
            Cases escalated for human review — low confidence, unknown failures, high-value payments, or policy
            violations.
          </p>
        </div>
        <span className="rounded-full border border-ink-600 bg-ink-800 px-3 py-1 font-mono text-xs text-mist-200">
          {items.length} pending
        </span>
      </div>

      {items.length === 0 ? (
        <Panel>
          <EmptyState
            title="Queue is empty"
            description="No cases currently need human review. Generate more payments or check back after a batch run."
          />
        </Panel>
      ) : (
        <div className="space-y-3">
          {items.map((item) => (
            <Panel key={item.payment_id} className="px-5 py-4">
              <div className="flex items-start justify-between gap-4">
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <Link
                      to={`/payments/${item.payment_id}`}
                      className="font-mono text-xs text-signal hover:underline"
                    >
                      {item.payment_id.slice(0, 8)}…
                    </Link>
                    <span className="text-2xs text-mist-400">{item.customer_external_id}</span>
                  </div>
                  <div className="mt-2 flex flex-wrap items-center gap-x-5 gap-y-1.5 text-xs">
                    <span className="font-mono font-semibold text-risk">{item.revenue_at_risk_display}</span>
                    <span className="text-mist-400">
                      diagnosis: <span className="text-mist-100">{item.diagnosis || "—"}</span>
                      {item.diagnosis_confidence != null && (
                        <span className="text-mist-400"> ({(item.diagnosis_confidence * 100).toFixed(0)}%)</span>
                      )}
                    </span>
                    <span className="text-mist-400">
                      proposed: <span className="text-mist-100">{item.proposed_action?.replaceAll("_", " ") || "—"}</span>
                    </span>
                  </div>
                  {item.policy_reason && (
                    <p className="mt-1.5 text-2xs text-mist-400">{item.policy_reason}</p>
                  )}
                </div>

                <div className="flex shrink-0 gap-1.5">
                  <Button
                    variant="primary"
                    disabled={actingOn === item.payment_id}
                    onClick={() => act(item.payment_id, "approve")}
                  >
                    Approve
                  </Button>
                  <Button
                    variant="ghost"
                    disabled={actingOn === item.payment_id}
                    onClick={() => act(item.payment_id, "reject")}
                  >
                    Reject
                  </Button>
                  <Button
                    variant="danger"
                    disabled={actingOn === item.payment_id}
                    onClick={() => act(item.payment_id, "stop")}
                  >
                    Stop
                  </Button>
                </div>
              </div>
            </Panel>
          ))}
        </div>
      )}
    </div>
  );
}
