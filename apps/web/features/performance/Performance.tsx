"use client";

import type { AnalyticsResponse, TradeStats } from "@apex/shared-types";
import { useState } from "react";
import { PageHeader } from "@/components/shell/PageHeader";
import { Callout, cx, EmptyState, Panel, Stat } from "@/components/ui/primitives";
import { CollapsiblePanel, EquityDrawdown, PnlCalendar } from "@/components/ui/viz";
import { usePolling } from "@/hooks/usePolling";
import { money } from "@/lib/money";

const pct = (v: number | null) => (v == null ? "—" : `${(v * 100).toFixed(0)}%`);

function Table({ rows, label }: { rows: (TradeStats & { key: string })[]; label: string }) {
  if (!rows.length) return <p className="text-sm text-faint">No trades.</p>;
  return (
    <div className="-mx-4 overflow-x-auto">
      <table className="w-full min-w-[460px] text-xs">
        <thead><tr className="text-left text-faint">{[label, "n", "Net", "Win", "PF", "Expectancy", "Max DD"].map((h) => <th key={h} className="px-2 py-1 font-medium first:pl-4">{h}</th>)}</tr></thead>
        <tbody className="font-mono">{rows.map((r) => (
          <tr key={r.key} className="border-t border-line/60">
            <td className="py-1 pr-2 pl-4 font-sans">{r.key}</td><td className={cx("px-2", r.trades < 30 && "text-warn")} title={r.trades < 30 ? "Small sample: uncertain" : undefined}>{r.trades}</td>
            <td className={cx("px-2", r.net_pnl >= 0 ? "text-up" : "text-down")}>{money(r.net_pnl, true)}</td>
            <td className="px-2">{pct(r.win_rate)}</td><td className="px-2">{r.profit_factor?.toFixed(2) ?? "—"}</td>
            <td className="px-2">{money(r.expectancy)}</td><td className="px-2">{money(r.max_drawdown)}</td>
          </tr>
        ))}</tbody>
      </table>
    </div>
  );
}

export function Performance() {
  const [period, setPeriod] = useState<"day" | "week" | "month">("day");
  const [days, setDays] = useState(90);
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [month, setMonth] = useState("");
  const range = start ? `&start=${start}${end ? `&end=${end}` : ""}` : "";
  const { data: a } = usePolling<AnalyticsResponse & {
    by_news: (TradeStats & { key: string })[];
    average_planned_rr: { value: number | null; trades_with_plan: number };
    rule_adherence: { trades_with_stop: number; exits_beyond_stop: number; stop_respected_rate: number | null; self_reported_followed_rate: number | null };
    common_findings: { code: string; title: string; count: number; net_pnl: number }[];
    sample_size_note: string | null;
    equity_curve: { t: string; trade_id: number; equity: number; drawdown: number }[];
    daily: { date: string; net: number; trades: number }[];
  }>(`/api/analytics?period=${period}&days=${days}${range}`, 60_000);
  const sel = "rounded border border-line bg-panel-2 px-2 py-1 text-xs";
  return (
    <>
      <PageHeader path="/performance">
        <div className="flex flex-wrap gap-2">
          <select aria-label="Report period" className={sel} value={period} onChange={(e) => setPeriod(e.target.value as typeof period)}>
            <option value="day">Daily</option><option value="week">Weekly</option><option value="month">Monthly</option>
          </select>
          <select aria-label="Window" className={sel} value={days} onChange={(e) => setDays(Number(e.target.value))}>
            <option value={30}>30 days</option><option value={90}>90 days</option><option value={365}>1 year</option><option value={3650}>All</option>
          </select>
          <input type="date" aria-label="From date" className={sel} value={start} onChange={(e) => setStart(e.target.value)} />
          <input type="date" aria-label="To date" className={sel} value={end} onChange={(e) => setEnd(e.target.value)} />
        </div>
      </PageHeader>
      {!a ? <p className="text-sm text-faint">Loading…</p> : a.overall.trades === 0 ? (
        <Panel><EmptyState title="No journal trades in this window">Import trades on the Trading Journal page, or close some paper trades.</EmptyState></Panel>
      ) : (
        <div className="space-y-4" data-testid="performance">
          {a.sample_size_note && <Callout tone="warn" title="Small sample">{a.sample_size_note}</Callout>}
          <Panel>
            <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-6">
              <Stat label="Trades" value={a.overall.trades} />
              <Stat label="Net P&L" value={<span className={a.overall.net_pnl >= 0 ? "text-up" : "text-down"}>{money(a.overall.net_pnl, true)}</span>} />
              <Stat label="Win rate" value={pct(a.overall.win_rate)} />
              <Stat label="Profit factor" value={a.overall.profit_factor?.toFixed(2) ?? "—"} />
              <Stat label="Avg win" value={money(a.overall.avg_win)} hint={`avg loss ${money(a.overall.avg_loss)}`} />
              <Stat label="Max drawdown" value={money(a.overall.max_drawdown)} />
            </div>
          </Panel>
          <div className="grid gap-4 xl:grid-cols-[3fr_2fr]">
            <CollapsiblePanel id="perf-equity" title="Equity curve and drawdown (journal P&L)">
              <EquityDrawdown points={a.equity_curve} />
              <p className="mt-1 text-[11px] text-faint">Cumulative net P&L of journal trades in this window; not the broker balance and excludes open-trade swings.</p>
            </CollapsiblePanel>
            <CollapsiblePanel id="perf-calendar" title="Daily P&L calendar">
              <PnlCalendar days={a.daily} month={month || (a.daily.at(-1)?.date.slice(0, 7) ?? new Date().toISOString().slice(0, 7))} onMonth={setMonth} />
            </CollapsiblePanel>
          </div>
          <div className="grid gap-4 xl:grid-cols-2">
            <Panel title={`${period[0]!.toUpperCase()}${period.slice(1)}ly report`}><Table rows={a.by_period} label={period} /></Panel>
            <Panel title="By strategy"><Table rows={a.by_strategy} label="Strategy" /></Panel>
            <Panel title="By instrument"><Table rows={a.by_symbol} label="Symbol" /></Panel>
            <Panel title="By entry hour (ET)"><Table rows={a.by_hour_et} label="Hour" /></Panel>
            <Panel title="Around news events"><Table rows={a.by_news} label="News proximity" /></Panel>
            <Panel title="Rule adherence and planning">
              <dl className="grid grid-cols-2 gap-y-1 text-sm" data-testid="adherence">
                <dt className="text-muted">Average planned reward:risk</dt><dd className="font-mono">{a.average_planned_rr.value ?? "—"} <span className="text-faint">({a.average_planned_rr.trades_with_plan} trades)</span></dd>
                <dt className="text-muted">Stop respected</dt><dd className="font-mono">{pct(a.rule_adherence.stop_respected_rate)} <span className="text-faint">({a.rule_adherence.trades_with_stop} with a stop)</span></dd>
                <dt className="text-muted">Exits beyond stop</dt><dd className="font-mono">{a.rule_adherence.exits_beyond_stop}</dd>
                <dt className="text-muted">Self-reported plan followed</dt><dd className="font-mono">{pct(a.rule_adherence.self_reported_followed_rate)}</dd>
              </dl>
              <p className="mt-3 text-xs font-medium text-muted uppercase">Most common execution findings</p>
              {a.common_findings.length === 0 ? <p className="text-sm text-faint">None yet.</p> : (
                <ul className="text-sm" data-testid="common-findings">{a.common_findings.slice(0, 6).map((f) => (
                  <li key={f.code} className="flex justify-between"><span>{f.title}</span><span className="font-mono text-muted">{f.count}× · {money(f.net_pnl, true)}</span></li>
                ))}</ul>
              )}
            </Panel>
          </div>
          <div className="grid gap-4 xl:grid-cols-2">
            <Panel title="Recurring mistakes">
              {a.mistakes.length === 0 ? <p className="text-sm text-faint">No mistakes tagged. Tag trades on the journal to analyse behaviour.</p> : (
                <ul className="space-y-2 text-sm" data-testid="mistakes">
                  {a.mistakes.map((m) => (
                    <li key={m.tag}>
                      <p><span className="text-warn">{m.tag.replaceAll("_", " ")}</span> · {m.count} trade(s) ({pct(m.share)}) · total {money(m.total_pnl, true)}</p>
                      <p className="text-xs text-muted">avg {money(m.avg_pnl_with)} with vs {money(m.avg_pnl_without)} without · {m.evidence}{m.welch.p != null ? ` (p = ${m.welch.p})` : ""}</p>
                    </li>
                  ))}
                </ul>
              )}
            </Panel>
            <Panel title="Planned vs actual">
              <dl className="grid grid-cols-2 gap-y-1 text-sm">
                <dt className="text-muted">Trades with a recorded plan</dt><dd className="font-mono">{a.planned_vs_actual.trades_with_plan} of {a.planned_vs_actual.trades_with_plan + a.planned_vs_actual.trades_without_plan}</dd>
                <dt className="text-muted">Average R (vs planned stop)</dt><dd className="font-mono">{a.planned_vs_actual.avg_r ?? "—"}</dd>
                <dt className="text-muted">Exits beyond the planned stop</dt><dd className="font-mono">{a.planned_vs_actual.exits_beyond_stop}</dd>
                <dt className="text-muted">Reached planned target</dt><dd className="font-mono">{a.planned_vs_actual.reached_target}</dd>
                <dt className="text-muted">Followed-plan rate</dt><dd className="font-mono">{pct(a.planned_vs_actual.followed_plan_rate)}</dd>
              </dl>
            </Panel>
          </div>
          <Panel title={`Rule checks (${a.rule_violations.length})`}>
            {!a.rules_verified && <div className="mb-3"><Callout tone="warn" title="Checked against UNVERIFIED rule values" /></div>}
            {a.rule_violations.length === 0 ? <p className="text-sm text-faint">No violations of the configured values found in these trades.</p> : (
              <ul className="space-y-1 text-sm" data-testid="violations">{a.rule_violations.map((v, i) => (
                <li key={i}><span className="font-mono text-xs text-faint">{v.date}</span> <span className="text-down">{v.rule}</span> <span className="text-muted">— {v.detail}</span></li>
              ))}</ul>
            )}
          </Panel>
          <ul className="list-disc space-y-0.5 pl-5 text-xs text-faint">{a.notes.map((n) => <li key={n}>{n}</li>)}</ul>
        </div>
      )}
    </>
  );
}
