import { NavLink, Outlet } from "react-router-dom";
import {
  LayoutDashboard, AlertTriangle, ListChecks, Receipt, Brain, ScrollText, Target, Settings as SettingsIcon,
} from "lucide-react";

const NAV = [
  { to: "/", label: "Overview", icon: LayoutDashboard, end: true },
  { to: "/at-risk", label: "At-Risk Revenue", icon: AlertTriangle },
  { to: "/queue", label: "Recovery Queue", icon: ListChecks },
  { to: "/payments", label: "Payments", icon: Receipt },
  { to: "/decisions", label: "AI Decisions", icon: Brain },
  { to: "/audit", label: "Audit Trail", icon: ScrollText },
  { to: "/evaluation", label: "Evaluation", icon: Target },
  { to: "/settings", label: "Settings", icon: SettingsIcon },
];

export default function Shell() {
  return (
    <div className="flex min-h-screen bg-ink-950">
      <aside className="flex w-60 shrink-0 flex-col border-r border-ink-600 bg-ink-900">
        <div className="flex items-center gap-2.5 border-b border-ink-600 px-5 py-5">
          <div className="flex h-7 w-7 items-center justify-center rounded-md bg-recovered/15">
            <span className="font-mono text-xs font-bold text-recovered">RR</span>
          </div>
          <div>
            <p className="text-sm font-semibold leading-none text-mist-50">RazorRecover</p>
            <p className="mt-0.5 text-2xs uppercase tracking-wider text-mist-400">AI Revenue Recovery</p>
          </div>
        </div>

        <nav className="flex-1 space-y-0.5 px-3 py-4">
          {NAV.map(({ to, label, icon: Icon, end }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              className={({ isActive }) =>
                `flex items-center gap-2.5 rounded-lg px-3 py-2 text-xs font-medium transition ${
                  isActive
                    ? "bg-recovered/10 text-recovered"
                    : "text-mist-300 hover:bg-ink-700 hover:text-mist-100"
                }`
              }
            >
              <Icon size={15} strokeWidth={2} />
              {label}
            </NavLink>
          ))}
        </nav>

        <div className="border-t border-ink-600 px-5 py-4">
          <p className="text-2xs text-mist-400">
            Simulated data only.<br />No real money moves.
          </p>
        </div>
      </aside>

      <main className="min-w-0 flex-1 overflow-y-auto">
        <Outlet />
      </main>
    </div>
  );
}
