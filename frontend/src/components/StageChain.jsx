const STAGE_LABELS = {
  PAYMENT_FAILED: "Payment Failed",
  RISK_DETECTED: "Risk Detected",
  AI_DIAGNOSED: "AI Diagnosed",
  AI_FALLBACK_USED: "Fallback Used",
  ACTION_PROPOSED: "Action Proposed",
  POLICY_APPROVED: "Policy Approved",
  POLICY_BLOCKED: "Policy Blocked",
  ACTION_EXECUTED: "Action Executed",
  PAYMENT_RECOVERED: "Recovered",
  ESCALATED: "Escalated",
  HUMAN_DECISION: "Human Decision",
  DUPLICATE_IGNORED: "Duplicate Ignored",
  ERROR: "Error",
};

const STAGE_TONE = {
  PAYMENT_FAILED: "blocked",
  RISK_DETECTED: "signal",
  AI_DIAGNOSED: "signal",
  AI_FALLBACK_USED: "mist",
  ACTION_PROPOSED: "signal",
  POLICY_APPROVED: "recovered",
  POLICY_BLOCKED: "blocked",
  ACTION_EXECUTED: "signal",
  PAYMENT_RECOVERED: "recovered",
  ESCALATED: "risk",
  HUMAN_DECISION: "risk",
  DUPLICATE_IGNORED: "mist",
  ERROR: "blocked",
};

const DOT_CLASSES = {
  blocked: "bg-blocked border-blocked",
  signal: "bg-signal border-signal",
  recovered: "bg-recovered border-recovered",
  risk: "bg-risk border-risk",
  mist: "bg-mist-400 border-mist-400",
};

const TEXT_CLASSES = {
  blocked: "text-blocked",
  signal: "text-signal",
  recovered: "text-recovered",
  risk: "text-risk",
  mist: "text-mist-400",
};

/**
 * Renders the immutable decision chain for one payment as a connected node
 * timeline -- this is the product's signature visual: the whole pitch is
 * "you can see exactly why the system did what it did," so the audit trail
 * gets to be the thing people actually look at, not a table buried at the
 * bottom of the page.
 */
export default function StageChain({ entries, orientation = "horizontal" }) {
  if (!entries || entries.length === 0) {
    return <p className="text-xs text-mist-400">No audit trail yet.</p>;
  }

  if (orientation === "vertical") {
    return (
      <ol className="relative ml-2 space-y-6 border-l border-ink-600 pl-6">
        {entries.map((entry, i) => {
          const tone = STAGE_TONE[entry.stage] || "mist";
          return (
            <li key={entry.id || i} className="relative">
              <span
                className={`absolute -left-[29px] top-0.5 h-2.5 w-2.5 rounded-full border-2 ${DOT_CLASSES[tone]} bg-ink-800`}
              />
              <div className="flex items-baseline justify-between gap-3">
                <p className={`text-xs font-semibold ${TEXT_CLASSES[tone]}`}>
                  {STAGE_LABELS[entry.stage] || entry.stage}
                </p>
                <time className="font-mono text-2xs text-mist-400">
                  {new Date(entry.timestamp).toLocaleTimeString()}
                </time>
              </div>
              {entry.payload && Object.keys(entry.payload).length > 0 && (
                <pre className="mt-1.5 max-w-full overflow-x-auto rounded-md bg-ink-900 px-2.5 py-2 font-mono text-2xs text-mist-300">
                  {JSON.stringify(entry.payload, null, 2)}
                </pre>
              )}
            </li>
          );
        })}
      </ol>
    );
  }

  return (
    <div className="flex flex-wrap items-center gap-x-1 gap-y-3">
      {entries.map((entry, i) => {
        const tone = STAGE_TONE[entry.stage] || "mist";
        return (
          <div key={entry.id || i} className="flex items-center">
            <div className="flex items-center gap-1.5 rounded-full border border-ink-600 bg-ink-900 px-2.5 py-1">
              <span className={`h-1.5 w-1.5 rounded-full ${DOT_CLASSES[tone]}`} />
              <span className={`text-2xs font-medium ${TEXT_CLASSES[tone]}`}>
                {STAGE_LABELS[entry.stage] || entry.stage}
              </span>
            </div>
            {i < entries.length - 1 && <span className="mx-1 text-ink-500">→</span>}
          </div>
        );
      })}
    </div>
  );
}
