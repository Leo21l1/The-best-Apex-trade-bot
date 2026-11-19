"use client";

import { useEffect, useState } from "react";
import { PageHeader } from "@/components/shell/PageHeader";
import { cx, Panel } from "@/components/ui/primitives";
import { usePolling } from "@/hooks/usePolling";
import { countdown, NEWS_TONE } from "@/lib/analyzer";
import { formatEt } from "@/lib/format";

interface NewsEvent {
  id: number; title: string; ts: string; time_et: string; date_et: string; impact: string; category: string;
  forecast: string | null; previous: string | null; actual: string | null; actual_note: string | null;
  source: string; minutes_until: number; priority: boolean;
}
interface Intel {
  as_of: string; status: string; summary: string; next_high_impact: NewsEvent | null; today: NewsEvent[];
  next_day: { date: string | null; events: NewsEvent[] };
  windows: { upcoming_minutes: number; pre_minutes: number; active_after_minutes: number; post_minutes: number };
  freshness: { source: string; last_success_at: string | null; last_attempt_at: string | null; next_attempt_at: string | null;
    last_error: string | null; age_hours: number | null; stale_after_hours: number; stale: boolean; note: string };
  direction_note: string;
}

const IMPACT: Record<string, string> = { high: "text-down border-down/50", medium: "text-warn border-warn/50", low: "text-faint border-line" };

/** Minutes until ``ts`` measured against the browser clock, refreshed every minute. */
function useMinuteClock(): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 60_000);
    return () => clearInterval(t);
  }, []);
  return now;
}

function EventTable({ events, now, testId }: { events: NewsEvent[]; now: number; testId: string }) {
  if (!events.length) return <p className="text-sm text-faint">No scheduled events.</p>;
  return (
    <div className="-mx-4 overflow-x-auto">
      <table className="w-full min-w-[640px] text-xs" data-testid={testId}>
        <thead><tr className="text-left text-faint">{["Time (ET)", "Event", "Impact", "Forecast", "Previous", "Actual", "When"].map((h) => <th key={h} className="px-2 py-1.5 font-medium first:pl-4">{h}</th>)}</tr></thead>
        <tbody>
          {events.map((e) => {
            const mins = (Date.parse(e.ts) - now) / 60_000;
            return (
              <tr key={e.id} className={cx("border-t border-line/40 hover:bg-panel-2/40", e.impact === "high" && "bg-down/[0.06] shadow-[inset_3px_0_0_0_rgba(239,68,68,0.7)]")}>
                <td className="px-2 py-1.5 pl-4 font-mono">{formatEt(e.ts)}</td>
                <td className="px-2 py-1.5">{e.title}{e.priority && <span className="ml-1 text-[10px] text-accent">key</span>}</td>
                <td className="px-2 py-1.5"><span className={cx("rounded border px-1.5 py-0.5 text-[10px] uppercase", IMPACT[e.impact])}>{e.impact}</span></td>
                <td className="px-2 py-1.5 font-mono">{e.forecast ?? "—"}</td>
                <td className="px-2 py-1.5 font-mono">{e.previous ?? "—"}</td>
                <td className="px-2 py-1.5 font-mono" title={e.actual_note ?? ""}>{e.actual ?? "—"}</td>
                <td className="px-2 py-1.5 font-mono">{countdown(mins)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/** Tab 2: today's and the next trading day's USD events, the current news-risk status and source freshness. */
export function News() {
  const { data, error } = usePolling<Intel>("/api/news/intel", 60_000);
  const now = useMinuteClock();
  if (error && !data) return <><PageHeader path="/news" /><Panel><p className="text-sm text-down">{error}</p></Panel></>;
  if (!data) return <><PageHeader path="/news" /><Panel><p className="text-sm text-faint">Loading the economic calendar…</p></Panel></>;
  const nx = data.next_high_impact;
  const f = data.freshness;
  return (
    <div className="space-y-4">
      <PageHeader path="/news" />
      <div className="grid gap-3 md:grid-cols-3">
        <Panel title="News risk now">
          <p className={cx("text-xl font-bold tracking-wide", NEWS_TONE[data.status])} data-testid="news-status">{data.status}</p>
          <p className={cx("mt-2 inline-block rounded-full border px-2.5 py-0.5 text-[11px] font-bold", data.status === "NEWS WINDOW ACTIVE" || data.status === "POST-NEWS VOLATILITY" ? "border-down/60 bg-down/10 text-down" : data.status.startsWith("NEWS DATA STALE") ? "border-warn/60 bg-warn/10 text-warn" : "border-line text-muted")} data-testid="restriction">
            {data.status === "NEWS WINDOW ACTIVE" || data.status === "POST-NEWS VOLATILITY" ? "Restriction window ACTIVE: no setup can confirm" : data.status.startsWith("NEWS DATA STALE") ? "Calendar stale: news risk UNKNOWN, no setup can confirm" : "No restriction window active"}
          </p>
          <p className="mt-1 text-sm text-muted">{data.summary}</p>
          <p className="mt-2 text-[11px] text-faint">Re-evaluated every 60 s · last {formatEt(data.as_of)}</p>
        </Panel>
        <Panel title="Next high-impact USD event">
          {nx ? (
            <>
              <p className="text-base font-semibold">{nx.title}</p>
              <p className="font-mono text-sm">{nx.time_et}</p>
              <p className="mt-2 font-mono text-4xl font-bold tracking-tight text-warn" data-testid="countdown">{countdown((Date.parse(nx.ts) - now) / 60_000)}</p>
            </>
          ) : <p className="text-sm text-faint">None in the loaded calendar.</p>}
        </Panel>
        <Panel title="Source freshness">
          <p className={cx("text-sm font-semibold", f.stale ? "text-warn" : "text-up")} data-testid="freshness">{f.stale ? "STALE / UNAVAILABLE" : "Up to date"}</p>
          <p className="text-xs text-muted">{f.source}</p>
          <p className="text-xs text-muted">Last successful download: {f.last_success_at ? `${formatEt(f.last_success_at, true)} (${f.age_hours} h ago)` : "never"}</p>
          {f.next_attempt_at && <p className="text-xs text-faint">Next check: {formatEt(f.next_attempt_at)}</p>}
          {f.last_error && <p className="text-xs text-warn">Last error: {f.last_error}</p>}
        </Panel>
      </div>
      <Panel title={`Today (${new Date(now).toLocaleDateString("en-US", { timeZone: "America/New_York", weekday: "long", month: "short", day: "numeric" })})`}>
        <EventTable events={data.today} now={now} testId="today-events" />
      </Panel>
      <Panel title={data.next_day.date ? `Next trading day (${data.next_day.date}) — high and medium impact` : "Next trading day"}>
        <EventTable events={data.next_day.events} now={now} testId="next-events" />
      </Panel>
      <p className="text-[11px] text-faint">
        Caution windows: upcoming from {data.windows.upcoming_minutes} min before; window active from {data.windows.pre_minutes} min before
        to {data.windows.active_after_minutes} min after; post-news volatility until {data.windows.post_minutes} min after. {data.direction_note} {f.note}
      </p>
    </div>
  );
}
