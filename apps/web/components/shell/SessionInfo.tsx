"use client";

import type { CalendarResponse, Readiness } from "@apex/shared-types";
import Link from "next/link";
import { useSyncExternalStore } from "react";
import { cx } from "@/components/ui/primitives";
import { usePolling } from "@/hooks/usePolling";
import { sessionState } from "@/lib/session";

const subscribe = (cb: () => void) => {
  const t = setInterval(cb, 15_000);
  return () => clearInterval(t);
};
const minute = () => Math.floor(Date.now() / 15_000);

export function SessionPill() {
  const tick = useSyncExternalStore(subscribe, minute, () => 0);
  // Authoritative: the server's session intelligence (CME holidays, early closes, maintenance).
  const { data } = usePolling<Readiness["session"]>("/api/session/context?symbol=ES", 60_000);
  if (!tick) return null;
  const fallback = sessionState(new Date()); // only until the server answers
  const open = data ? data.market.open : fallback.open;
  const label = data ? (open ? data.window.label : `Closed: ${data.market.reason}`) : fallback.label;
  return (
    <span title={data ? `${data.ny_time} · next: ${data.next_transition.label} at ${data.next_transition.at_et} ET` : undefined}
      className={cx("hidden max-w-64 items-center gap-1.5 truncate rounded border px-2 py-0.5 text-[11px] lg:inline-flex", open ? "border-up/30 text-up" : "border-line text-muted")} data-testid="session-pill">
      <span className={cx("h-1.5 w-1.5 shrink-0 rounded-full", open ? "bg-up" : "bg-faint")} aria-hidden />{label}
    </span>
  );
}

/** Countdown to the next high-impact calendar event (from imported official calendars). */
export function NextEvent() {
  const { data } = usePolling<CalendarResponse>("/api/calendar?days=2&back_hours=0", 60_000);
  const next = data?.events.find((e) => e.importance === "high" && e.minutes_until >= -e.window.post_minutes);
  if (!next) return null;
  const m = Math.round(next.minutes_until);
  const label = next.in_window ? "IN WINDOW" : m < 90 ? `${m}m` : `${Math.round(m / 60)}h`;
  return (
    <Link href="/calendar" title={next.title} data-testid="next-event"
      className={cx("hidden max-w-56 truncate rounded border px-2 py-0.5 text-[11px] xl:inline-block", next.in_window ? "border-warn/60 bg-warn/10 text-warn" : "border-line text-muted hover:text-text")}>
      {next.title} · {label}
    </Link>
  );
}
