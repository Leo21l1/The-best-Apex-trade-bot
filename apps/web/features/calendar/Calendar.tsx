"use client";

import type { CalendarResponse, EconomicEvent, IcsImportResponse } from "@apex/shared-types";
import { useCallback, useEffect, useMemo, useState } from "react";
import { PageHeader } from "@/components/shell/PageHeader";
import { Callout, cx, EmptyState, Panel } from "@/components/ui/primitives";
import { useSession } from "@/hooks/useSession";
import { api, ApiError } from "@/lib/api";
import { MARKET_TZ } from "@/lib/format";

const IMPORTANCE: Record<EconomicEvent["importance"], string> = {
  high: "text-down border-down/40",
  medium: "text-warn border-warn/40",
  low: "text-muted border-line",
};
const dayFmt = new Intl.DateTimeFormat("en-US", { timeZone: MARKET_TZ, weekday: "long", month: "short", day: "numeric" });
const timeFmt = new Intl.DateTimeFormat("en-US", { timeZone: MARKET_TZ, hour: "2-digit", minute: "2-digit", hour12: false });
const input = "w-full rounded-md border border-line bg-panel-2 px-2.5 py-1.5 text-sm outline-none focus:border-accent";

function countdown(min: number): string {
  if (min < -1) return `${Math.round(-min)} min ago`;
  if (min <= 1) return "now";
  if (min < 90) return `in ${Math.round(min)} min`;
  if (min < 48 * 60) return `in ${Math.round(min / 60)} h`;
  return `in ${Math.round(min / 1440)} d`;
}

export function Calendar() {
  const { user } = useSession();
  const admin = user?.role === "admin";
  const [data, setData] = useState<CalendarResponse | null>(null);
  const [minImportance, setMinImportance] = useState<"low" | "medium" | "high">("medium");
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setData(await api<CalendarResponse>("/api/calendar?days=14&back_hours=12"));
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);
  useEffect(() => {
    // load() sets state only after its await resolves.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load();
    const t = setInterval(() => void load(), 60_000);
    return () => clearInterval(t);
  }, [load]);

  const rank = { low: 0, medium: 1, high: 2 };
  const groups = useMemo(() => {
    const out = new Map<string, EconomicEvent[]>();
    for (const e of data?.events ?? []) {
      if (rank[e.importance] < rank[minImportance]) continue;
      const k = dayFmt.format(new Date(e.ts));
      out.set(k, [...(out.get(k) ?? []), e]);
    }
    return [...out.entries()];
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, minImportance]);

  return (
    <>
      <PageHeader path="/calendar" />
      {error && <div className="mb-4"><Callout tone="danger" title="Request failed">{error}</Callout></div>}
      {data && (
        <div className="mb-4">
          <Callout tone="warn" title="Apex news-trading restrictions are unverified">
            {data.apex_restrictions} Alert window: {data.window.pre_minutes} min before to {data.window.post_minutes} min
            after each high/medium event. {data.note}
          </Callout>
        </div>
      )}

      <div className="grid gap-4 xl:grid-cols-[1fr_22rem]">
        <Panel
          title="Upcoming events (times in ET)"
          actions={
            <select aria-label="Minimum importance" className="rounded border border-line bg-panel-2 px-2 py-1 text-xs" value={minImportance}
              onChange={(e) => setMinImportance(e.target.value as typeof minImportance)}>
              <option value="high">High only</option>
              <option value="medium">Medium and high</option>
              <option value="low">All</option>
            </select>
          }
        >
          {!data ? (
            <p className="text-sm text-faint">Loading…</p>
          ) : data.empty_calendar ? (
            <EmptyState title="No calendar imported">
              PROJECT APEX does not invent event times. Import an official calendar file (for example the BLS release
              schedule .ics, or the Federal Reserve FOMC calendar) or add events manually.
            </EmptyState>
          ) : groups.length === 0 ? (
            <EmptyState title="No events in the next 14 days at this importance" />
          ) : (
            <div className="space-y-5" data-testid="calendar-events">
              {groups.map(([day, events]) => (
                <div key={day}>
                  <p className="mb-2 text-xs font-medium tracking-wide text-muted uppercase">{day}</p>
                  <ul className="space-y-1.5">
                    {events.map((e) => (
                      <li key={e.id} className={cx("flex flex-wrap items-center gap-x-3 gap-y-1 rounded-md border px-3 py-2",
                        e.in_window ? "border-warn/60 bg-warn/5" : "border-line")}>
                        <span className="w-12 font-mono text-sm">{timeFmt.format(new Date(e.ts))}</span>
                        <span className={cx("rounded border px-1.5 py-px text-[10px] uppercase", IMPORTANCE[e.importance])}>{e.importance}</span>
                        <span className="min-w-0 flex-1 text-sm">{e.title}</span>
                        <span className="font-mono text-[11px] text-faint">{e.affected.join(" ") || "no mapped markets"}</span>
                        <span className={cx("text-xs", e.in_window ? "text-warn" : "text-muted")}>
                          {e.in_window ? "IN WINDOW · " : ""}{countdown(e.minutes_until)}
                        </span>
                        {admin && (
                          <button type="button" aria-label={`Delete ${e.title}`} className="text-xs text-faint hover:text-down"
                            onClick={async () => {
                              if (!window.confirm(`Delete "${e.title}"?`)) return;
                              await api(`/api/calendar/events/${e.id}`, { method: "DELETE" }).catch(() => undefined);
                              void load();
                            }}>
                            ✕
                          </button>
                        )}
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
            </div>
          )}
        </Panel>

        {admin ? (
          <div className="space-y-4">
            <IcsImport onDone={load} />
            <ManualEvent onDone={load} />
          </div>
        ) : (
          <Panel title="Manage calendar"><p className="text-sm text-faint">Only administrators can import or add events.</p></Panel>
        )}
      </div>
    </>
  );
}

function IcsImport({ onDone }: { onDone: () => void }) {
  const [text, setText] = useState("");
  const [name, setName] = useState("uploaded file");
  const [res, setRes] = useState<IcsImportResponse | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const run = async (dry: boolean) => {
    setErr(null);
    try {
      const r = await api<IcsImportResponse>("/api/calendar/import-ics", { method: "POST", body: { ics: text, source_name: name, dry_run: dry } });
      setRes(r);
      if (!dry) onDone();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Import failed");
    }
  };
  return (
    <Panel title="Import official calendar (.ics)">
      <input type="file" accept=".ics,text/calendar" aria-label="Calendar file"
        className="mb-2 block text-xs text-muted file:mr-3 file:rounded file:border file:border-line file:bg-panel-2 file:px-2 file:py-1 file:text-muted"
        onChange={async (e) => {
          const f = e.target.files?.[0];
          if (f) { setText(await f.text()); setName(f.name); setRes(null); }
        }} />
      <textarea aria-label="ICS content" className={cx(input, "h-24 font-mono text-[11px]")} placeholder="BEGIN:VCALENDAR…" value={text}
        onChange={(e) => { setText(e.target.value); setRes(null); }} />
      <div className="mt-2 flex gap-2">
        <button type="button" className="rounded-md border border-line px-3 py-1.5 text-sm text-muted hover:bg-panel-2 disabled:opacity-50" disabled={!text} onClick={() => void run(true)}>Preview</button>
        <button type="button" className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-bg disabled:opacity-50" disabled={!res?.dry_run || res.parsed === 0} onClick={() => void run(false)}>
          Import {res?.dry_run ? res.parsed : ""} events
        </button>
      </div>
      {err && <p className="mt-2 text-sm text-down">{err}</p>}
      {res && (
        <div className="mt-2 text-xs text-muted" data-testid="ics-result">
          {res.dry_run ? `${res.parsed} event(s) parsed.` : `Imported ${res.written} event(s).`}
          {res.errors.length > 0 && <span className="text-warn"> {res.errors.length} skipped.</span>}
          {res.dry_run && res.preview.slice(0, 4).map((p) => <p key={p.ts + p.title} className="truncate font-mono">{p.importance} · {p.title}</p>)}
        </div>
      )}
      <p className="mt-2 text-[11px] text-faint">Duplicates are merged by event UID, so re-importing is safe.</p>
    </Panel>
  );
}

function ManualEvent({ onDone }: { onDone: () => void }) {
  const [title, setTitle] = useState("");
  const [when, setWhen] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setMsg(null);
    // datetime-local is wall time in New York: convert by asking the browser for the offset of that instant.
    const local = new Date(`${when}:00`);
    const nyOffset = new Date(local.toLocaleString("en-US", { timeZone: MARKET_TZ })).getTime() - local.getTime();
    const ts = new Date(local.getTime() - nyOffset).toISOString();
    try {
      await api("/api/calendar/events", { method: "POST", body: { ts, title } });
      setTitle("");
      setMsg("Event added.");
      onDone();
    } catch (err) {
      setMsg(err instanceof ApiError ? err.message : "Failed");
    }
  };
  return (
    <Panel title="Add event manually">
      <form onSubmit={submit} className="space-y-2">
        <label className="block text-xs text-muted">Title<input className={input} value={title} onChange={(e) => setTitle(e.target.value)} placeholder="e.g. CPI (November)" /></label>
        <label className="block text-xs text-muted">Date and time (New York)<input type="datetime-local" className={input} value={when} onChange={(e) => setWhen(e.target.value)} /></label>
        <button type="submit" disabled={!title || !when} className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-bg disabled:opacity-50">Add</button>
        {msg && <p className="text-xs text-muted">{msg}</p>}
        <p className="text-[11px] text-faint">Importance and affected markets are classified from the title.</p>
      </form>
    </Panel>
  );
}
