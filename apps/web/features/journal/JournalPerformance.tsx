"use client";

import { useState } from "react";
import { cx, Panel } from "@/components/ui/primitives";
import { usePolling } from "@/hooks/usePolling";
import { money } from "@/lib/money";

interface Row { key: string; trades: number; net_pnl: number; win_rate: number | null; profit_factor: number | null; avg_r?: number | null; expectancy: number | null; max_drawdown: number }
interface Analytics { by_period: Row[]; by_strategy: Row[]; by_symbol: Row[]; by_hour_et: Row[] }

const VIEWS = [["by_period", "Period"], ["by_strategy", "Strategy"], ["by_symbol", "Instrument"], ["by_hour_et", "Hour (ET)"]] as const;

/** Performance summaries computed by the server from recorded trades only (no sample data). */
export function JournalPerformance({ refreshKey }: { refreshKey: number }) {
  const [period, setPeriod] = useState<"day" | "week" | "month">("week");
  const [view, setView] = useState<(typeof VIEWS)[number][0]>("by_period");
  const { data } = usePolling<Analytics>(`/api/analytics?period=${period}&days=3650&r=${refreshKey}`, 120_000);
  const rows = (data?.[view] ?? []).slice().reverse().slice(0, 24);
  return (
    <Panel title="Performance" actions={
      <div className="flex flex-wrap gap-1 text-[11px]">
        {VIEWS.map(([k, l]) => (
          <button key={k} type="button" onClick={() => setView(k)} aria-pressed={view === k}
            className={cx("rounded-full px-2.5 py-1", view === k ? "bg-accent/20 text-text" : "text-muted hover:text-text")}>{l}</button>
        ))}
        {view === "by_period" && (
          <select aria-label="Period" className="rounded-full border border-line bg-panel-2 px-2 py-0.5" value={period} onChange={(e) => setPeriod(e.target.value as "day" | "week" | "month")}>
            <option value="day">Daily</option><option value="week">Weekly</option><option value="month">Monthly</option>
          </select>
        )}
      </div>
    }>
      {!data ? <p className="text-sm text-faint">Loading…</p> : rows.length === 0 ? (
        <p className="text-sm text-faint">No recorded trades yet. Performance appears here once you record trades.</p>
      ) : (
        <div className="-mx-4 overflow-x-auto">
          <table className="w-full min-w-[560px] text-xs" data-testid="journal-performance">
            <thead><tr className="text-left">{["", "Trades", "Net P&L", "Win rate", "Profit factor", "Expectancy", "Max DD"].map((h) => <th key={h} className="apex-label px-2 py-1.5 font-medium first:pl-4">{h}</th>)}</tr></thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.key} className="border-t border-line/40 font-mono hover:bg-panel-2/40">
                  <td className="px-2 py-1.5 pl-4 font-sans">{r.key}</td>
                  <td className="px-2 py-1.5">{r.trades}</td>
                  <td className={cx("px-2 py-1.5", r.net_pnl >= 0 ? "text-up" : "text-down")}>{money(r.net_pnl, true)}</td>
                  <td className="px-2 py-1.5">{r.win_rate == null ? "—" : `${(r.win_rate * 100).toFixed(0)}%`}</td>
                  <td className="px-2 py-1.5">{r.profit_factor?.toFixed(2) ?? "—"}</td>
                  <td className="px-2 py-1.5">{money(r.expectancy)}</td>
                  <td className="px-2 py-1.5">{money(r.max_drawdown)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="mt-2 text-[11px] text-faint">Computed from your recorded trades only; small samples are noisy.</p>
    </Panel>
  );
}
