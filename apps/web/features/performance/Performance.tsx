"use client";

import type { AnalyticsResponse, TradeStats } from "@apex/shared-types";
import { useState } from "react";
import { PageHeader } from "@/components/shell/PageHeader";
import { Callout, cx, EmptyState, Panel, Stat } from "@/components/ui/primitives";
import { usePolling } from "@/hooks/usePolling";
import { money } from "@/lib/money";

const pct = (v: number | null) => (v == null ? "—" : `${(v * 100).toFixed(0)}%`);

function Table({ rows, label }: { rows: (TradeStats & { key: string })[]; label: string }) {
  if (!rows.length) return <p className="text-sm text-faint">No trades.</p>;
  return (
    <div className="-mx-4 overflow-x-auto">
      <table className="w-full min-w-[460px] text-xs">
        <thead><tr className="text-left text-faint">{[label, "Trades", "Net", "Win", "PF", "Expectancy", "Max DD"].map((h) => <th key={h} className="px-2 py-1 font-medium first:pl-4">{h}</th>)}</tr></thead>
        <tbody className="font-mono">{rows.map((r) => (
          <tr key={r.key} className="border-t border-line/60">
            <td className="py-1 pr-2 pl-4 font-sans">{r.key}</td><td className="px-2">{r.trades}</td>
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
  const { data: a } = usePolling<AnalyticsResponse>(`/api/analytics?period=${period}&days=${days}`, 60_000);
  const sel = "rounded border border-line bg-panel-2 px-2 py-1 text-xs";
  return (
    <>
      <PageHeader path="/performance">
        <div className="flex gap-2">
          <select aria-label="Report period" className={sel} value={period} onChange={(e) => setPeriod(e.target.value as typeof period)}>
            <option value="day">Daily</option><option value="week">Weekly</option><option value="month">Monthly</option>
          </select>
          <select aria-label="Window" className={sel} value={days} onChange={(e) => setDays(Number(e.target.value))}>
            <option value={30}>30 days</option><option value={90}>90 days</option><option value={365}>1 year</option><option value={3650}>All</option>
          </select>
        </div>
      </PageHeader>
      {!a ? <p className="text-sm text-faint">Loading…</p> : a.overall.trades === 0 ? (
        <Panel><EmptyState title="No journal trades in this window">Import trades on the Trading Journal page, or close some paper trades.</EmptyState></Panel>
      ) : (
        <div className="space-y-4" data-testid="performance">
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
          <div className="grid gap-4 xl:grid-cols-2">
            <Panel title={`${period[0]!.toUpperCase()}${period.slice(1)}ly report`}><Table rows={a.by_period} label={period} /></Panel>
            <Panel title="By strategy"><Table rows={a.by_strategy} label="Strategy" /></Panel>
            <Panel title="By instrument"><Table rows={a.by_symbol} label="Symbol" /></Panel>
            <Panel title="By entry hour (ET)"><Table rows={a.by_hour_et} label="Hour" /></Panel>
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
