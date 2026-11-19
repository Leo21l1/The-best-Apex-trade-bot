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
const TAB_SHORT: Record<string, string> = { "/": "Analyzer", "/news": "News", "/journal": "Journal" };

/** Top bar: brand, the three primary tabs, data status, alerts, clock and the settings gear. */
export function TopBar({ onPalette }: { onPalette: () => void }) {
  const pathname = usePathname();
  const { user, logout } = useSession();
  const { status, connection } = useSystemStatus();
  return (
    <header className="sticky top-0 z-30 border-b border-line/80 bg-[#060a12]/80 backdrop-blur-xl">
      <div className="mx-auto flex h-16 max-w-[1400px] items-center gap-3 px-4 sm:px-6">
        <Link href="/" className="flex shrink-0 items-center gap-2.5" aria-label="PROJECT APEX home">
          <ApexMark />
          <span className="hidden leading-tight sm:block">
            <span className="apex-gradient-text block text-[15px] font-bold tracking-[0.18em]">PROJECT APEX</span>
            <span className="block text-[10px] tracking-[0.16em] text-faint uppercase">Futures Market Intelligence</span>
          </span>
        </Link>
        <nav aria-label="Primary" className="ml-1 flex min-w-0 flex-1 items-center gap-1 overflow-x-auto rounded-full border border-line/70 bg-panel/60 p-1 sm:ml-6 sm:flex-none">
          {TABS.map((href) => {
            const active = href === "/" ? pathname === "/" : pathname.startsWith(href);
            return (
              <Link
                key={href}
                href={href}
                aria-label={TAB_LABEL[href]}
                aria-current={active ? "page" : undefined}
                className={cx(
                  "shrink-0 rounded-full px-2.5 py-2 text-[10.5px] font-semibold tracking-[0.06em] uppercase transition-colors sm:px-3.5 sm:text-[11px] sm:tracking-[0.12em]",
                  active
                    ? "bg-gradient-to-r from-accent/20 to-accent-2/20 text-text shadow-[inset_0_0_0_1px_rgba(34,211,238,0.45)]"
                    : "text-muted hover:bg-panel-2/70 hover:text-text",
                )}
              >
                <span className="sm:hidden" aria-hidden="true">{TAB_SHORT[href]}</span>
                <span className="hidden sm:inline">{TAB_LABEL[href] ?? NAV.find((n) => n.href === href)?.label}</span>
              </Link>
            );
          })}
        </nav>
        <div className="ml-auto flex shrink-0 items-center gap-2 sm:gap-3">
          <span className="hidden rounded border border-accent/40 bg-accent/10 px-2 py-0.5 font-mono text-[11px] tracking-wider text-accent md:inline"
            title="No broker connection. PROJECT APEX never places, cancels or modifies orders." data-testid="execution-mode">
            ANALYSIS ONLY
          </span>
          <span className="hidden items-center gap-1.5 lg:flex" title={status?.components.find((c) => c.name === "market_data")?.detail}>
            <span className="text-xs text-faint">Data</span>
            <QualityBadge quality={status?.market_data_quality ?? "unavailable"} />
          </span>
          <span className={cx("hidden h-2 w-2 rounded-full sm:inline-block", connection === "open" ? "bg-up" : connection === "connecting" ? "bg-warn" : "bg-down")}
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

/** Original PROJECT APEX mark: an ascending apex over a price line (lightweight inline SVG). */
function ApexMark() {
  return (
    <svg width="34" height="34" viewBox="0 0 40 40" aria-hidden="true" className="shrink-0">
      <defs>
        <linearGradient id="apexg" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#22d3ee" />
          <stop offset="1" stopColor="#3b82f6" />
        </linearGradient>
      </defs>
      <rect x="1" y="1" width="38" height="38" rx="10" fill="#0e1626" stroke="#1e2b42" />
      <path d="M8 29 L20 9 L32 29" fill="none" stroke="url(#apexg)" strokeWidth="3" strokeLinejoin="round" strokeLinecap="round" />
      <path d="M13 24 L18 20 L22 23 L28 16" fill="none" stroke="#10b981" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
