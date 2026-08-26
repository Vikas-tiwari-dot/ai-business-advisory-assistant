import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { Panel, PanelHeader, LoadingState, ErrorState } from "../components/ui";

export default function Settings() {
  const [health, setHealth] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    api.health().then(setHealth).catch((e) => setError(e.message));
  }, []);

  if (error) return <ErrorState message={error} />;
  if (!health) return <LoadingState label="Checking system health…" />;

  return (
    <div className="max-w-2xl space-y-6 p-8">
      <div>
        <h1 className="text-lg font-semibold text-mist-50">Settings</h1>
        <p className="mt-0.5 text-xs text-mist-400">System status and what's real vs. simulated in this demo.</p>
      </div>

      <Panel>
        <PanelHeader title="System status" />
        <dl className="divide-y divide-ink-700">
          {[
            ["Service", health.app_name],
            ["Version", health.version],
            ["Environment", health.environment],
            ["Database", `${health.database_dialect} (${health.db})`],
            ["AI provider", health.ai_provider === "none" ? "none (deterministic fallback rules)" : health.ai_provider],
          ].map(([label, value]) => (
            <div key={label} className="flex items-center justify-between px-5 py-3">
              <dt className="text-xs text-mist-400">{label}</dt>
              <dd className="font-mono text-xs text-mist-100">{value}</dd>
            </div>
          ))}
        </dl>
      </Panel>

      <Panel>
        <PanelHeader title="What's real vs. simulated" />
        <div className="space-y-3 px-5 py-4 text-xs leading-relaxed text-mist-300">
          <p>
            <span className="font-semibold text-mist-100">Payment data</span> — always synthetic, generated locally
            with a seeded random generator. No real customer data is ever used.
          </p>
          <p>
            <span className="font-semibold text-mist-100">Money movement</span> — always simulated via configurable
            probability draws (per diagnosis category). No real money moves under any configuration.
          </p>
          <p>
            <span className="font-semibold text-mist-100">AI diagnosis & recovery proposals</span> — real LLM calls
            when an API key is configured (Gemini or OpenAI), otherwise deterministic fallback rules. Both paths are
            fully audited and labeled.
          </p>
          <p>
            <span className="font-semibold text-mist-100">Razorpay Test Mode</span> — when configured, creates real
            Test Mode Payment Links. Since Razorpay has no "retry a failed payment" concept, this reports the action
            as initiated, not as confirmed revenue recovered.
          </p>
        </div>
      </Panel>

      <Panel>
        <PanelHeader title="Configuration" subtitle="Set via environment variables — see .env.example" />
        <div className="px-5 py-4 text-xs text-mist-400">
          AI_PROVIDER, GEMINI_API_KEY / OPENAI_API_KEY, RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET,
          MAX_RECOVERY_ATTEMPTS, LOW_CONFIDENCE_THRESHOLD, HIGH_VALUE_ESCALATION_THRESHOLD
        </div>
      </Panel>
    </div>
  );
}
