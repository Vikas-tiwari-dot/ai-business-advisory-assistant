import { useEffect, useState } from "react";
import { useParams, Link } from "react-router-dom";
import { ArrowLeft } from "lucide-react";
import { api } from "../lib/api";
import { Panel, PanelHeader, StatusPill, LoadingState, ErrorState } from "../components/ui";
import StageChain from "../components/StageChain";

export default function PaymentDetail() {
  const { id } = useParams();
  const [detail, setDetail] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    setDetail(null);
    setError(null);
    api.getPayment(id).then(setDetail).catch((e) => setError(e.message));
  }, [id]);

  if (error) return <ErrorState message={error} />;
  if (!detail) return <LoadingState label="Loading payment…" />;

  const { payment, attempts, analyses, actions, audit_trail } = detail;

  return (
    <div className="space-y-6 p-8">
      <Link to="/payments" className="inline-flex items-center gap-1.5 text-xs text-mist-400 hover:text-mist-100">
        <ArrowLeft size={13} /> Back to payments
      </Link>

      <div className="flex items-start justify-between">
        <div>
          <h1 className="font-mono text-lg font-semibold text-mist-50">{payment.id}</h1>
          <p className="mt-1 text-xs text-mist-400">
            {payment.customer_external_id} · {payment.razorpay_payment_id || "no gateway ref"}
          </p>
        </div>
        <div className="text-right">
          <p className="font-mono text-2xl font-semibold text-mist-50">{payment.amount_display}</p>
          <div className="mt-1 flex justify-end gap-1.5">
            <StatusPill status={payment.status} />
            {payment.case_status && <StatusPill status={payment.case_status} />}
          </div>
        </div>
      </div>

      <Panel>
        <PanelHeader title="Decision timeline" subtitle="Complete, immutable audit chain for this payment" />
        <div className="px-5 py-5">
          <StageChain entries={audit_trail} orientation="vertical" />
        </div>
      </Panel>

      <div className="grid grid-cols-2 gap-5">
        <Panel>
          <PanelHeader title="AI analyses" subtitle="Diagnosis and action-proposal stages" />
          <div className="divide-y divide-ink-700">
            {analyses.length === 0 && <p className="px-5 py-4 text-xs text-mist-400">No AI analysis recorded.</p>}
            {analyses.map((a, i) => (
              <div key={i} className="px-5 py-3.5">
                <div className="flex items-center justify-between">
                  <span className="text-2xs font-semibold uppercase tracking-wide text-mist-300">
                    {a.stage.replaceAll("_", " ")}
                  </span>
                  <span className="font-mono text-2xs text-mist-400">{a.model_provider}</span>
                </div>
                {a.diagnosis && (
                  <p className="mt-1 text-xs text-mist-100">
                    {a.diagnosis} <span className="text-mist-400">({(a.confidence * 100).toFixed(0)}% confidence)</span>
                  </p>
                )}
                <p className="mt-1 text-2xs text-mist-400">{a.reasoning_summary}</p>
                {!a.schema_valid && (
                  <p className="mt-1 text-2xs font-medium text-blocked">AI output failed validation — fallback used</p>
                )}
              </div>
            ))}
          </div>
        </Panel>

        <Panel>
          <PanelHeader title="Recovery actions" subtitle="Proposed → policy → execution" />
          <div className="divide-y divide-ink-700">
            {actions.length === 0 && <p className="px-5 py-4 text-xs text-mist-400">No action recorded.</p>}
            {actions.map((a, i) => (
              <div key={i} className="px-5 py-3.5">
                <div className="flex items-center justify-between">
                  <span className="text-xs font-semibold text-mist-100">{a.proposed_action.replaceAll("_", " ")}</span>
                  <StatusPill status={a.policy_allowed ? "recovered" : "escalated"} />
                </div>
                <p className="mt-1 text-2xs text-mist-400">{a.policy_reason}</p>
                {a.executed && (
                  <p className="mt-1.5 text-2xs text-mist-300">
                    Execution: <StatusPill status={a.execution_result} />{" "}
                    {a.revenue_recovered > 0 && (
                      <span className="font-mono text-recovered">{a.revenue_recovered_display}</span>
                    )}
                  </p>
                )}
                {a.human_override && (
                  <p className="mt-1 text-2xs text-signal">Human override: {a.human_override}</p>
                )}
              </div>
            ))}
          </div>
        </Panel>
      </div>

      <Panel>
        <PanelHeader title="Payment attempts" subtitle="Raw attempt history" />
        <table className="w-full text-left text-xs">
          <thead>
            <tr className="border-b border-ink-600 text-2xs uppercase tracking-wide text-mist-400">
              <th className="px-5 py-2.5 font-medium">#</th>
              <th className="px-5 py-2.5 font-medium">Status</th>
              <th className="px-5 py-2.5 font-medium">Failure code</th>
              <th className="px-5 py-2.5 font-medium">Reason</th>
              <th className="px-5 py-2.5 font-medium">Time</th>
            </tr>
          </thead>
          <tbody>
            {attempts.map((a) => (
              <tr key={a.attempt_number} className="border-b border-ink-700 last:border-0">
                <td className="px-5 py-2.5 font-mono">{a.attempt_number}</td>
                <td className="px-5 py-2.5">
                  <StatusPill status={a.status} />
                </td>
                <td className="px-5 py-2.5 font-mono text-mist-300">{a.failure_code || "—"}</td>
                <td className="px-5 py-2.5 text-mist-400">{a.failure_reason || "—"}</td>
                <td className="px-5 py-2.5 font-mono text-2xs text-mist-400">
                  {new Date(a.timestamp).toLocaleString()}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Panel>
    </div>
  );
}
