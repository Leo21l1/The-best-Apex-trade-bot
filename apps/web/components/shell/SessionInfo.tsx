"use client";

import type { CalendarResponse } from "@apex/shared-types";
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
  if (!tick) return null;
  const s = sessionState(new Date());
  return (
    <span className={cx("hidden items-center gap-1.5 rounded border px-2 py-0.5 text-[11px] lg:inline-flex", s.open ? "border-up/30 text-up" : "border-line text-muted")} data-testid="session-pill">
      <span className={cx("h-1.5 w-1.5 rounded-full", s.open ? "bg-up" : "bg-faint")} />{s.label}
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
