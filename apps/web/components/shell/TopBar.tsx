"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useSession } from "@/hooks/useSession";
import { useSystemStatus } from "@/hooks/useSystemStatus";
import { NAV, SETTINGS_HREF, TABS } from "@/lib/nav";
import { AlertsBell } from "./AlertsBell";
import { Clock } from "./Clock";
import { cx, QualityBadge } from "@/components/ui/primitives";

const TAB_LABEL: Record<string, string> = { "/": "Market Analyzer", "/news": "News", "/journal": "Trading Journal" };

/** Top bar: brand, the three primary tabs, data status, alerts, clock and the settings gear. */
export function TopBar({ onPalette }: { onPalette: () => void }) {
  const pathname = usePathname();
  const { user, logout } = useSession();
  const { status, connection } = useSystemStatus();
  return (
    <header className="sticky top-0 z-30 border-b border-line bg-bg/95 backdrop-blur">
      <div className="flex h-14 items-center gap-3 px-4">
        <Link href="/" className="flex shrink-0 items-center gap-2" aria-label="PROJECT APEX home">
          <span className="grid h-7 w-7 place-items-center rounded bg-accent/15 font-mono text-sm font-semibold text-accent">A</span>
          <span className="hidden text-sm font-semibold tracking-wide sm:inline">PROJECT APEX</span>
        </Link>
        <nav aria-label="Primary" className="flex min-w-0 flex-1 items-center gap-1 overflow-x-auto">
          {TABS.map((href) => {
            const active = href === "/" ? pathname === "/" : pathname.startsWith(href);
            return (
              <Link
                key={href}
                href={href}
                aria-current={active ? "page" : undefined}
                className={cx(
                  "shrink-0 rounded-md px-3 py-1.5 text-xs font-semibold tracking-wider uppercase",
                  active ? "bg-panel-2 text-text" : "text-muted hover:bg-panel-2/60 hover:text-text",
                )}
              >
                {TAB_LABEL[href] ?? NAV.find((n) => n.href === href)?.label}
              </Link>
            );
          })}
        </nav>
        <div className="flex shrink-0 items-center gap-2 sm:gap-3">
          <span className="hidden rounded border border-accent/40 bg-accent/10 px-2 py-0.5 font-mono text-[11px] tracking-wider text-accent md:inline"
            title="No broker connection. PROJECT APEX never places, cancels or modifies orders." data-testid="execution-mode">
            ANALYSIS ONLY
          </span>
          <span className="hidden items-center gap-1.5 lg:flex" title={status?.components.find((c) => c.name === "market_data")?.detail}>
            <span className="text-xs text-faint">Data</span>
            <QualityBadge quality={status?.market_data_quality ?? "unavailable"} />
          </span>
          <span className={cx("h-2 w-2 rounded-full", connection === "open" ? "bg-up" : connection === "connecting" ? "bg-warn" : "bg-down")}
            title={`Realtime channel: ${connection}`} aria-label={`Realtime channel ${connection}`} />
          {user?.role === "viewer" && (
            <span className="rounded border border-line px-2 py-0.5 text-[11px] text-muted" title="Viewers cannot change data">Read-only</span>
          )}
          <AlertsBell />
          <span className="hidden sm:inline"><Clock /></span>
          <button type="button" onClick={onPalette} className="hidden rounded px-1.5 py-1 font-mono text-[11px] text-faint hover:bg-panel-2 hover:text-muted md:inline" title="Search (Ctrl K)">⌘K</button>
          <Link href={SETTINGS_HREF} aria-label="Settings and health" title="Settings & health"
            className={cx("rounded p-1.5 hover:bg-panel-2", pathname.startsWith(SETTINGS_HREF) ? "text-text" : "text-muted")}>
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
              <path d="M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6Z" />
              <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09a1.65 1.65 0 0 0-1-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09a1.65 1.65 0 0 0 1.51-1 1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1Z" />
            </svg>
          </Link>
          {user && (
            <button type="button" onClick={() => void logout()} className="hidden rounded px-2 py-1 text-xs text-muted hover:bg-panel-2 hover:text-text sm:inline" title={user.email}>
              Sign out
            </button>
          )}
        </div>
      </div>
    </header>
  );
}
