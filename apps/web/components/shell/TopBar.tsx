"use client";

import { useSession } from "@/hooks/useSession";
import { useSystemStatus } from "@/hooks/useSystemStatus";
import { Clock } from "./Clock";
import { cx, QualityBadge, StatePill } from "@/components/ui/primitives";

export function TopBar({
  onMenu,
  onPalette,
}: {
  onMenu: () => void;
  onPalette: () => void;
}) {
  const { user, logout } = useSession();
  const { status, connection } = useSystemStatus();
  const market = status?.components.find((c) => c.name === "market_data");
  const rules = status?.components.find((c) => c.name === "apex_rule_profile");

  return (
    <header className="sticky top-0 z-30 flex h-14 items-center gap-3 border-b border-line bg-bg/95 px-4 backdrop-blur">
      <button
        type="button"
        onClick={onMenu}
        className="rounded p-1.5 text-muted hover:bg-panel-2 lg:hidden"
        aria-label="Open navigation"
      >
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <path d="M4 6h16M4 12h16M4 18h16" />
        </svg>
      </button>

      <button
        type="button"
        onClick={onPalette}
        className="flex w-full max-w-xs items-center justify-between rounded-md border border-line bg-panel px-3 py-1.5 text-sm text-faint hover:text-muted"
      >
        <span>Search pages…</span>
        <span className="hidden font-mono text-[11px] sm:inline">Ctrl K</span>
      </button>

      <div className="ml-auto flex items-center gap-2 sm:gap-3">
        <span
          title="PROJECT APEX never places, cancels or modifies live orders."
          className="hidden rounded border border-accent/40 bg-accent/10 px-2 py-0.5 font-mono text-[11px] tracking-wider text-accent md:inline"
          data-testid="execution-mode"
        >
          {status?.execution_mode === "ANALYSIS_ONLY" || !status ? "ANALYSIS ONLY" : status.execution_mode}
        </span>
        <span className="hidden items-center gap-1.5 sm:flex" title={market?.detail}>
          <span className="text-xs text-faint">Data</span>
          <QualityBadge quality={status?.market_data_quality ?? "unavailable"} />
        </span>
        {rules && rules.state !== "ok" && (
          <span className="hidden xl:inline" title={rules.detail}>
            <StatePill state="degraded" label="Rules unverified" />
          </span>
        )}
        <span
          className={cx(
            "h-2 w-2 rounded-full",
            connection === "open" ? "bg-up" : connection === "connecting" ? "bg-warn" : "bg-down",
          )}
          title={`Realtime channel: ${connection}`}
          aria-label={`Realtime channel ${connection}`}
        />
        <Clock />
        {user && (
          <button
            type="button"
            onClick={() => void logout()}
            className="rounded px-2 py-1 text-xs text-muted hover:bg-panel-2 hover:text-text"
            title={user.email}
          >
            Sign out
          </button>
        )}
      </div>
    </header>
  );
}
