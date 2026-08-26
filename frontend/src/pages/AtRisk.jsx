import { useEffect, useState } from "react";
import { AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid, Legend } from "recharts";
import { api } from "../lib/api";
import { Panel, PanelHeader, StatCard, LoadingState, ErrorState, EmptyState } from "../components/ui";

export default function AtRisk() {
  const [metrics, setMetrics] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    api.getMetrics().then(setMetrics).catch((e) => setError(e.message));
  }, []);

  if (error) return <ErrorState message={error} />;
  if (!metrics) return <LoadingState label="Loading at-risk revenue…" />;

  const chartData = metrics.revenue_over_time.map((d) => ({
    date: d.date,
    "At risk": Math.round(d.at_risk / 100),
    Recovered: Math.round(d.recovered / 100),
  }));

  return (
    <div className="space-y-6 p-8">
      <div>
        <h1 className="text-lg font-semibold text-mist-50">At-Risk Revenue</h1>
        <p className="mt-0.5 text-xs text-mist-400">Exposure over time and by failure category.</p>
      </div>

      <div className="grid grid-cols-3 gap-4">
        <StatCard label="Currently at risk" value={metrics.revenue_at_risk_display} tone="risk" />
        <StatCard label="Recovered to date" value={metrics.revenue_recovered_display} tone="recovered" />
        <StatCard label="Recovery rate" value={`${(metrics.recovery_rate * 100).toFixed(1)}%`} />
      </div>

      <Panel>
        <PanelHeader title="Revenue at risk vs. recovered" subtitle="₹ by day, batch-generation date" />
        <div className="h-72 px-4 py-4">
          {chartData.length === 0 ? (
            <EmptyState title="No trend data yet" description="Generate payments from the Overview page first." />
          ) : (
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={chartData}>
                <defs>
                  <linearGradient id="atRisk" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#F5A623" stopOpacity={0.35} />
                    <stop offset="95%" stopColor="#F5A623" stopOpacity={0} />
                  </linearGradient>
                  <linearGradient id="recovered" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#2DD4BF" stopOpacity={0.4} />
                    <stop offset="95%" stopColor="#2DD4BF" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="#1B2430" vertical={false} />
                <XAxis dataKey="date" stroke="#5B6B7F" fontSize={11} />
                <YAxis stroke="#5B6B7F" fontSize={11} tickFormatter={(v) => `₹${v.toLocaleString()}`} />
                <Tooltip
                  contentStyle={{ background: "#141B24", border: "1px solid #26303D", borderRadius: 8, fontSize: 12 }}
                  formatter={(v) => `₹${v.toLocaleString()}`}
                />
                <Legend wrapperStyle={{ fontSize: 12 }} />
                <Area type="monotone" dataKey="At risk" stroke="#F5A623" fill="url(#atRisk)" strokeWidth={2} />
                <Area type="monotone" dataKey="Recovered" stroke="#2DD4BF" fill="url(#recovered)" strokeWidth={2} />
              </AreaChart>
            </ResponsiveContainer>
          )}
        </div>
      </Panel>

      <Panel>
        <PanelHeader title="By failure category" subtitle="Count of diagnosed payments" />
        <div className="grid grid-cols-2 gap-3 px-5 py-5 sm:grid-cols-4">
          {metrics.failure_categories.map((c) => (
            <div key={c.category} className="rounded-lg border border-ink-600 bg-ink-900 px-3 py-3">
              <p className="text-2xs uppercase tracking-wide text-mist-400">{c.category.replaceAll("_", " ")}</p>
              <p className="mt-1 font-mono text-lg font-semibold text-mist-50">{c.count}</p>
            </div>
          ))}
        </div>
      </Panel>
    </div>
  );
}
