export function Panel({ children, className = "" }) {
  return (
    <div className={`rounded-xl border border-ink-600 bg-ink-800 shadow-panel ${className}`}>
      {children}
    </div>
  );
}

export function PanelHeader({ title, subtitle, action }) {
  return (
    <div className="flex items-center justify-between border-b border-ink-600 px-5 py-4">
      <div>
        <h2 className="text-sm font-semibold tracking-wide text-mist-50">{title}</h2>
        {subtitle && <p className="mt-0.5 text-2xs uppercase tracking-wider text-mist-400">{subtitle}</p>}
      </div>
      {action}
    </div>
  );
}

export function StatCard({ label, value, sublabel, tone = "default", mono = true }) {
  const toneClasses = {
    default: "text-mist-50",
    recovered: "text-recovered",
    risk: "text-risk",
    blocked: "text-blocked",
    signal: "text-signal",
  };
  return (
    <Panel className="px-5 py-4">
      <p className="text-2xs font-medium uppercase tracking-wider text-mist-400">{label}</p>
      <p className={`mt-2 text-2xl font-semibold ${mono ? "font-mono font-mono-nums" : ""} ${toneClasses[tone]}`}>
        {value}
      </p>
      {sublabel && <p className="mt-1 text-xs text-mist-400">{sublabel}</p>}
    </Panel>
  );
}

const STATUS_STYLES = {
  recovered: "bg-recovered/15 text-recovered border-recovered/30",
  open: "bg-signal/15 text-signal border-signal/30",
  escalated: "bg-risk/15 text-risk border-risk/30",
  stopped: "bg-mist-400/15 text-mist-300 border-mist-400/30",
  closed_unrecovered: "bg-blocked/15 text-blocked border-blocked/30",
  failed: "bg-blocked/15 text-blocked border-blocked/30",
  success: "bg-recovered/15 text-recovered border-recovered/30",
  skipped: "bg-mist-400/15 text-mist-300 border-mist-400/30",
  pending: "bg-signal/15 text-signal border-signal/30",
};

export function StatusPill({ status }) {
  const cls = STATUS_STYLES[status] || "bg-ink-600 text-mist-300 border-ink-500";
  return (
    <span className={`inline-flex items-center rounded-full border px-2.5 py-0.5 text-2xs font-medium uppercase tracking-wide ${cls}`}>
      {status?.replaceAll("_", " ") || "—"}
    </span>
  );
}

export function Button({ children, onClick, variant = "primary", disabled, className = "" }) {
  const variants = {
    primary: "bg-recovered text-ink-950 hover:bg-recovered/90",
    danger: "bg-blocked text-ink-950 hover:bg-blocked/90",
    ghost: "bg-transparent border border-ink-600 text-mist-100 hover:bg-ink-700",
    signal: "bg-signal text-white hover:bg-signal/90",
  };
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className={`rounded-lg px-3.5 py-1.5 text-xs font-semibold transition disabled:cursor-not-allowed disabled:opacity-40 ${variants[variant]} ${className}`}
    >
      {children}
    </button>
  );
}

export function EmptyState({ title, description }) {
  return (
    <div className="flex flex-col items-center justify-center gap-1 py-16 text-center">
      <p className="text-sm font-medium text-mist-200">{title}</p>
      {description && <p className="max-w-sm text-xs text-mist-400">{description}</p>}
    </div>
  );
}

export function LoadingState({ label = "Loading…" }) {
  return (
    <div className="flex items-center justify-center gap-2 py-16 text-xs text-mist-400">
      <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-recovered" />
      {label}
    </div>
  );
}

export function ErrorState({ message }) {
  return (
    <div className="flex flex-col items-center justify-center gap-1 py-16 text-center">
      <p className="text-sm font-medium text-blocked">Something didn't load</p>
      <p className="max-w-sm text-xs text-mist-400">{message}</p>
    </div>
  );
}
