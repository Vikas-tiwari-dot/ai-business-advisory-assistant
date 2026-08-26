import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../lib/api";
import { Panel, StatusPill, LoadingState, ErrorState, EmptyState, Button } from "../components/ui";

const STATUS_OPTIONS = ["", "failed", "recovered", "unrecoverable", "pending", "created"];

export default function Payments() {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [status, setStatus] = useState("");
  const [page, setPage] = useState(1);
  const pageSize = 15;

  useEffect(() => {
    setError(null);
    const params = { page, page_size: pageSize };
    if (status) params.status = status;
    api.listPayments(params).then(setData).catch((e) => setError(e.message));
  }, [status, page]);

  if (error) return <ErrorState message={error} />;

  const totalPages = data ? Math.max(1, Math.ceil(data.total / pageSize)) : 1;

  return (
    <div className="space-y-6 p-8">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold text-mist-50">Payments</h1>
          <p className="mt-0.5 text-xs text-mist-400">All ingested payment events and their current status.</p>
        </div>
        <select
          value={status}
          onChange={(e) => {
            setStatus(e.target.value);
            setPage(1);
          }}
          className="rounded-lg border border-ink-600 bg-ink-800 px-3 py-1.5 text-xs text-mist-100 outline-none focus:border-recovered"
        >
          {STATUS_OPTIONS.map((s) => (
            <option key={s} value={s}>
              {s === "" ? "All statuses" : s}
            </option>
          ))}
        </select>
      </div>

      <Panel>
        {!data ? (
          <LoadingState label="Loading payments…" />
        ) : data.items.length === 0 ? (
          <EmptyState title="No payments found" description="Try a different filter, or generate data from Overview." />
        ) : (
          <>
            <table className="w-full text-left text-xs">
              <thead>
                <tr className="border-b border-ink-600 text-2xs uppercase tracking-wide text-mist-400">
                  <th className="px-5 py-3 font-medium">Payment</th>
                  <th className="px-5 py-3 font-medium">Customer</th>
                  <th className="px-5 py-3 font-medium">Amount</th>
                  <th className="px-5 py-3 font-medium">Status</th>
                  <th className="px-5 py-3 font-medium">Case</th>
                  <th className="px-5 py-3 font-medium">Risk score</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((p) => (
                  <tr key={p.id} className="border-b border-ink-700 last:border-0 hover:bg-ink-700/40">
                    <td className="px-5 py-3">
                      <Link to={`/payments/${p.id}`} className="font-mono text-signal hover:underline">
                        {p.id.slice(0, 8)}…
                      </Link>
                    </td>
                    <td className="px-5 py-3 text-mist-300">{p.customer_external_id}</td>
                    <td className="px-5 py-3 font-mono font-medium text-mist-50">{p.amount_display}</td>
                    <td className="px-5 py-3">
                      <StatusPill status={p.status} />
                    </td>
                    <td className="px-5 py-3">{p.case_status ? <StatusPill status={p.case_status} /> : "—"}</td>
                    <td className="px-5 py-3 font-mono text-mist-300">
                      {p.risk_score != null ? p.risk_score.toFixed(2) : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>

            <div className="flex items-center justify-between border-t border-ink-600 px-5 py-3">
              <p className="text-2xs text-mist-400">
                {data.total} total · page {page} of {totalPages}
              </p>
              <div className="flex gap-1.5">
                <Button variant="ghost" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>
                  Previous
                </Button>
                <Button variant="ghost" disabled={page >= totalPages} onClick={() => setPage((p) => p + 1)}>
                  Next
                </Button>
              </div>
            </div>
          </>
        )}
      </Panel>
    </div>
  );
}
