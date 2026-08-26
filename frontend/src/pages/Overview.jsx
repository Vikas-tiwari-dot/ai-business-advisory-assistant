import { useEffect, useState } from "react";
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, PieChart, Pie, Cell, CartesianGrid,
} from "recharts";
import { api } from "../lib/api";
import { Panel, PanelHeader, StatCard, Button, LoadingState, ErrorState } from "../components/ui";

const PIE_COLORS = ["#2DD4BF", "#F5A623", "#F87171", "#6366F1", "#8B98A9", "#5B6B7F", "#3A4655", "#26303D"];

export default function Overview() {
  const [metrics, setMetrics] = useState(null);
  const [error, setError] = useState(null);
  const [generating, setGenerating] = useState(false);

  const load = () => {
    setError(null);
    api.getMetrics().then(setMetrics).catch((e) => setError(e.message));
  };

  useEffect(load, []);

  const handleGenerate = async () => {
    setGenerating(true);
    try {
      await api.generateSimulation(300);
      load();
    } catch (e) {
      setError(e.message);
    } finally {
      setGenerating(false);
    }
  };

  if (error) return <ErrorState message={error} />;
  if (!metrics) return <LoadingState label="Loading overview…" />;

  const hasData = metrics.payments_recovered + metrics.pending_recovery + metrics.human_escalations > 0;

  return (
    <div className="space-y-6 p-8">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold text-mist-50">Overview</h1>
          <p className="mt-0.5 text-xs text-mist-400">Live operational snapshot of the recovery pipeline.</p>
        </div>
        <Button onClick={handleGenerate} disabled={generating}>
          {generating ? "Generating…" : "Generate 300 payments"}
        </Button>
      </div>

      {!hasData && (
        <Panel className="px-6 py-10 text-center">
          <p className="text-sm font-medium text-mist-100">No data yet</p>
          <p className="mx-auto mt-1 max-w-sm text-xs text-mist-400">
            Generate a batch of synthetic payment events to populate the dashboard. Runs entirely on the local
            deterministic fallback rules or your configured AI provider — no real money involved.
          </p>
        </Panel>
      )}

      <div className="grid grid-cols-4 gap-4">
        <StatCard label="Revenue at risk" value={metrics.revenue_at_risk_display} tone="risk" />
        <StatCard label="Revenue recovered" value={metrics.revenue_recovered_display} tone="recovered" />
        <StatCard label="Recovery rate" value={`${(metrics.recovery_rate * 100).toFixed(1)}%`} tone="recovered" />
        <StatCard label="Payments recovered" value={metrics.payments_recovered} />
        <StatCard label="Pending recovery" value={metrics.pending_recovery} tone="signal" />
        <StatCard label="Human escalations" value={metrics.human_escalations} tone="risk" />
        <StatCard label="Blocked actions" value={metrics.blocked_actions} tone="blocked" />
        <StatCard label="Failure categories" value={metrics.failure_categories.length} mono={false} />
      </div>

      <div className="grid grid-cols-2 gap-5">
        <Panel>
          <PanelHeader title="Failure categories" subtitle="Diagnoses across all processed payments" />
          <div className="h-64 px-4 py-4">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={metrics.failure_categories} layout="vertical" margin={{ left: 8 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#1B2430" horizontal={false} />
                <XAxis type="number" stroke="#5B6B7F" fontSize={11} />
                <YAxis
                  type="category"
                  dataKey="category"
                  stroke="#5B6B7F"
                  fontSize={10.5}
                  width={110}
                  tickFormatter={(v) => v.replaceAll("_", " ")}
                />
                <Tooltip
                  contentStyle={{ background: "#141B24", border: "1px solid #26303D", borderRadius: 8, fontSize: 12 }}
                  labelStyle={{ color: "#DCE3EA" }}
                />
                <Bar dataKey="count" fill="#6366F1" radius={[0, 4, 4, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </Panel>

        <Panel>
          <PanelHeader title="Recovery outcomes" subtitle="What happened to executed actions" />
          <div className="flex h-64 items-center justify-center px-4 py-4">
            <ResponsiveContainer width="100%" height="100%">
              <PieChart>
                <Pie
                  data={metrics.recovery_outcomes}
                  dataKey="count"
                  nameKey="outcome"
                  innerRadius={55}
                  outerRadius={85}
                  paddingAngle={3}
                >
                  {metrics.recovery_outcomes.map((entry, i) => (
                    <Cell key={entry.outcome} fill={PIE_COLORS[i % PIE_COLORS.length]} />
                  ))}
                </Pie>
                <Tooltip
                  contentStyle={{ background: "#141B24", border: "1px solid #26303D", borderRadius: 8, fontSize: 12 }}
                />
              </PieChart>
            </ResponsiveContainer>
          </div>
        </Panel>
      </div>

      <Panel>
        <PanelHeader title="Recovery actions" subtitle="Which bounded action the agent chose" />
        <div className="h-56 px-4 py-4">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={metrics.recovery_actions_breakdown}>
              <CartesianGrid strokeDasharray="3 3" stroke="#1B2430" vertical={false} />
              <XAxis dataKey="action" stroke="#5B6B7F" fontSize={10} tickFormatter={(v) => v.replaceAll("_", " ")} />
              <YAxis stroke="#5B6B7F" fontSize={11} />
              <Tooltip
                contentStyle={{ background: "#141B24", border: "1px solid #26303D", borderRadius: 8, fontSize: 12 }}
              />
              <Bar dataKey="count" fill="#2DD4BF" radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </Panel>
    </div>
  );
}
