"use client";

import type { JournalResponse, JournalTrade } from "@apex/shared-types";
import { useCallback, useEffect, useState } from "react";
import { PageHeader } from "@/components/shell/PageHeader";
import { Callout, cx, EmptyState, Panel, Stat } from "@/components/ui/primitives";
import { CollapsiblePanel, PnlCalendar } from "@/components/ui/viz";
import { api, ApiError } from "@/lib/api";
import { BrokerImport } from "./BrokerImport";
import { RecordTrade } from "./RecordTrade";
import { DeletedTrades, TradeEditor } from "./TradeEditor";
import { formatEt } from "@/lib/format";
import { money } from "@/lib/money";

const STRATEGIES = ["orb", "vwap_trend", "sweep_reversal", "breakout_retest", "momentum_pullback", "vol_expansion", "mean_reversion", "mtf_trend", "smc_sweep_mss", "smc_ob_retest", "smc_fvg_retest", "discretionary"];
const input = "w-full rounded-md border border-line bg-panel-2 px-2.5 py-1.5 text-sm outline-none focus:border-accent";

export function dailyPnl(trades: JournalTrade[]): { date: string; net: number; trades: number }[] {
  const m = new Map<string, { date: string; net: number; trades: number }>();
  for (const t of trades) {
    const d = m.get(t.trading_date) ?? { date: t.trading_date, net: 0, trades: 0 };
    d.net = Math.round((d.net + t.net_pnl) * 100) / 100;
    d.trades += 1;
    m.set(t.trading_date, d);
  }
  return [...m.values()].sort((a, b) => a.date.localeCompare(b.date));
}

interface ImportResult { dry_run: boolean; layout: string | null; parsed: number; duplicates: number; imported: number; errors: string[] }

export function Journal() {
  const [data, setData] = useState<JournalResponse | null>(null);
  const [source, setSource] = useState("");
  const [open, setOpen] = useState<number | null>(null);
  const [month, setMonth] = useState("");
  const [editing, setEditing] = useState<JournalTrade | null>(null);
  const [deletedKey, setDeletedKey] = useState(0);
  const load = useCallback(async () => {
    setData(await api<JournalResponse>(`/api/journal/trades${source ? `?source=${source}` : ""}`).catch(() => null));
  }, [source]);
  useEffect(() => {
    // load() sets state only after its await resolves.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load();
  }, [load]);

  const s = data?.stats;
  return (
    <>
      <PageHeader path="/journal" />
      <div className="grid gap-4 xl:grid-cols-[1fr_22rem]">
        <div className="min-w-0 space-y-4">
          {s && (
            <Panel>
              <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-6" data-testid="journal-stats">
                <Stat label="Trades" value={s.trades} />
                <Stat label="Net P&L" value={<span className={s.net_pnl >= 0 ? "text-up" : "text-down"}>{money(s.net_pnl, true)}</span>} hint={`fees ${money(s.fees)}`} />
                <Stat label="Win rate" value={s.win_rate == null ? "—" : `${(s.win_rate * 100).toFixed(1)}%`} />
                <Stat label="Profit factor" value={s.profit_factor?.toFixed(2) ?? "—"} />
                <Stat label="Expectancy" value={money(s.expectancy)} />
                <Stat label="Max drawdown" value={money(s.max_drawdown)} />
                <Stat label="Gross P&L" value={money(s.gross_pnl ?? null, true)} />
                <Stat label="Avg win / loss" value={`${money(s.avg_win)} / ${money(s.avg_loss)}`} />
                <Stat label="Average R" value={s.avg_r == null ? "—" : `${s.avg_r}R`} hint={s.r_trades ? `${s.r_trades} trades with a stop` : "needs a stop-loss"} />
                <Stat label="Best streak" value={s.max_consecutive_wins ?? "—"} hint="consecutive wins" />
                <Stat label="Worst streak" value={s.max_consecutive_losses ?? "—"} hint="consecutive losses" />
              </div>
            </Panel>
          )}
          {data && data.trades.length > 0 && (
            <CollapsiblePanel id="journal-calendar" title="Calendar (daily net P&L)">
              <PnlCalendar days={dailyPnl(data.trades)} month={month || data.trades[0]!.trading_date.slice(0, 7)} onMonth={setMonth} />
            </CollapsiblePanel>
          )}
          <Panel title="Trades" actions={
            <select aria-label="Source filter" className="rounded border border-line bg-panel-2 px-2 py-1 text-xs" value={source} onChange={(e) => setSource(e.target.value)}>
              <option value="">All sources</option><option value="csv">Imported</option><option value="paper">Paper</option><option value="manual">Manual</option>
            </select>
          }>
            {!data ? <p className="text-sm text-faint">Loading…</p> : data.trades.length === 0 ? (
              <EmptyState title="No trades yet">Import a Tradovate Performance export (CSV). Closed paper trades appear here automatically.</EmptyState>
            ) : (
              <div className="-mx-4 overflow-x-auto">
                <table className="w-full min-w-[760px] text-xs" data-testid="journal-table">
                  <thead><tr className="text-left text-faint">{["Exit (ET)", "Symbol", "Side", "Qty", "Entry", "Exit", "Net", "Strategy", "Plan", "Mistakes", "Src", ""].map((h) => <th key={h} className="px-2 py-1.5 font-medium first:pl-4">{h}</th>)}</tr></thead>
                  <tbody>
                    {data.trades.map((t) => (
                      <TradeRow key={t.id} t={t} tags={data.mistake_tags} open={open === t.id} onToggle={() => setOpen(open === t.id ? null : t.id)} onSaved={load} onEdit={() => setEditing(t)} />
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Panel>
        </div>
        <div className="min-w-0 space-y-4">
          <RecordTrade onSaved={load} />
          <ImportPanel onDone={load} />
          <DeletedTrades key={deletedKey} onRestored={load} />
        </div>
        <div className="min-w-0 xl:col-span-2"><BrokerImport onImported={load} /></div>
      </div>
      {editing && <TradeEditor trade={editing} onClose={() => setEditing(null)} onSaved={() => { void load(); setDeletedKey((k) => k + 1); }} />}
    </>
  );
}

function TradeRow({ t, tags, open, onToggle, onSaved, onEdit }: { t: JournalTrade; tags: string[]; open: boolean; onToggle: () => void; onSaved: () => void; onEdit: () => void }) {
  const [edit, setEdit] = useState({ strategy_id: t.strategy_id ?? "", planned_stop: t.planned_stop?.toString() ?? "", planned_target: t.planned_target?.toString() ?? "", followed_plan: t.followed_plan, mistakes: t.mistakes, notes: t.notes ?? "" });
  const [err, setErr] = useState<string | null>(null);
  const save = async () => {
    setErr(null);
    try {
      await api(`/api/journal/trades/${t.id}`, { method: "PATCH", body: {
        strategy_id: edit.strategy_id || null, planned_stop: edit.planned_stop ? Number(edit.planned_stop) : null,
        planned_target: edit.planned_target ? Number(edit.planned_target) : null, followed_plan: edit.followed_plan,
        mistakes: edit.mistakes, notes: edit.notes || null,
      } });
      onSaved();
      onToggle();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Save failed");
    }
  };
  return (
    <>
      <tr className="cursor-pointer border-t border-line/60 font-mono hover:bg-panel-2/40" onClick={onToggle}>
        <td className="py-1.5 pr-2 pl-4">{formatEt(t.exit_time, true)}</td>
        <td className="px-2">{t.symbol}</td>
        <td className={cx("px-2", t.direction === "long" ? "text-up" : "text-down")}>{t.direction}</td>
        <td className="px-2">{t.quantity}</td><td className="px-2">{t.entry_price}</td><td className="px-2">{t.exit_price}</td>
        <td className={cx("px-2", t.net_pnl >= 0 ? "text-up" : "text-down")}>{money(t.net_pnl, true)}</td>
        <td className="px-2 font-sans text-muted">{t.strategy_id ?? "—"}</td>
        <td className="px-2 font-sans">{t.followed_plan == null ? "—" : t.followed_plan ? "✓" : "✗"}</td>
        <td className="px-2 font-sans text-warn">{t.mistakes.join(", ")}</td>
        <td className="px-2 font-sans text-faint">{t.source === "paper" ? "paper (sim)" : t.source}</td>
        <td className="px-2 whitespace-nowrap">
          <a href={`/trade?id=${t.id}`} onClick={(e) => e.stopPropagation()} className="font-sans text-accent hover:underline" data-testid={`review-${t.id}`}>Review</a>
          <button type="button" onClick={(e) => { e.stopPropagation(); onEdit(); }} className="ml-2 font-sans text-accent hover:underline" data-testid={`edit-${t.id}`} aria-label={`Edit trade ${t.id}`}>Edit</button>
          {(t.corrected_fields?.length ?? 0) > 0 && <span className="ml-1 font-sans text-[10px] text-warn" title={`Manually corrected: ${t.corrected_fields!.join(", ")}`}>✎ corrected</span>}
        </td>
      </tr>
      {open && (
        <tr className="border-t border-line/60 bg-panel-2/30">
          <td colSpan={12} className="px-4 py-3">
            <div className="grid gap-3 lg:grid-cols-4">
              <label className="text-xs text-muted">Strategy
                <select aria-label="Trade strategy" className={input} value={edit.strategy_id} onChange={(e) => setEdit({ ...edit, strategy_id: e.target.value })}>
                  <option value="">Untagged</option>{STRATEGIES.map((x) => <option key={x}>{x}</option>)}
                </select>
              </label>
              <label className="text-xs text-muted">Planned stop<input aria-label="Planned stop" className={input} value={edit.planned_stop} onChange={(e) => setEdit({ ...edit, planned_stop: e.target.value })} /></label>
              <label className="text-xs text-muted">Planned target<input aria-label="Planned target" className={input} value={edit.planned_target} onChange={(e) => setEdit({ ...edit, planned_target: e.target.value })} /></label>
              <label className="text-xs text-muted">Followed plan?
                <select aria-label="Followed plan" className={input} value={edit.followed_plan == null ? "" : String(edit.followed_plan)} onChange={(e) => setEdit({ ...edit, followed_plan: e.target.value === "" ? null : e.target.value === "true" })}>
                  <option value="">Not recorded</option><option value="true">Yes</option><option value="false">No</option>
                </select>
              </label>
            </div>
            <fieldset className="mt-3">
              <legend className="text-xs text-muted">Mistakes</legend>
              <div className="mt-1 flex flex-wrap gap-2">
                {tags.map((tag) => (
                  <label key={tag} className={cx("cursor-pointer rounded border px-2 py-0.5 text-xs", edit.mistakes.includes(tag) ? "border-warn/60 text-warn" : "border-line text-muted")}>
                    <input type="checkbox" className="sr-only" checked={edit.mistakes.includes(tag)} onChange={(e) => setEdit({ ...edit, mistakes: e.target.checked ? [...edit.mistakes, tag] : edit.mistakes.filter((m) => m !== tag) })} />
                    {tag.replaceAll("_", " ")}
                  </label>
                ))}
              </div>
            </fieldset>
            <label className="mt-3 block text-xs text-muted">Notes<textarea aria-label="Trade notes" className={cx(input, "h-16")} value={edit.notes} onChange={(e) => setEdit({ ...edit, notes: e.target.value })} /></label>
            <div className="mt-2 flex items-center gap-3">
              <button type="button" className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-bg" onClick={() => void save()}>Save</button>
              <span className="text-[11px] text-faint">Gross {money(t.gross_pnl, true)} · fees {money(t.fees)} · entry {formatEt(t.entry_time, true)}</span>
              {err && <span className="text-sm text-down">{err}</span>}
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

function ImportPanel({ onDone }: { onDone: () => void }) {
  const [text, setText] = useState("");
  const [res, setRes] = useState<ImportResult | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const run = async (dry: boolean) => {
    setErr(null);
    try {
      const r = await api<ImportResult>("/api/journal/import", { method: "POST", body: { csv: text, dry_run: dry } });
      setRes(r);
      if (!dry) onDone();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Import failed");
    }
  };
  return (
    <Panel title="Import trades (CSV)">
      <p className="mb-2 text-xs text-muted">
        Tradovate: Account Reports → Performance → export CSV. Also accepted: <code>symbol, side, qty, entry_time, exit_time, entry_price, exit_price</code> [, fees, pnl].
        Times without a zone are read as New York time. Re-importing skips trades already in the journal.
      </p>
      <input type="file" accept=".csv,text/csv" aria-label="Trades file"
        className="mb-2 block text-xs text-muted file:mr-3 file:rounded file:border file:border-line file:bg-panel-2 file:px-2 file:py-1 file:text-muted"
        onChange={async (e) => { const f = e.target.files?.[0]; if (f) { setText(await f.text()); setRes(null); } }} />
      <textarea aria-label="Trades CSV" className={cx(input, "h-28 font-mono text-[11px]")} value={text} onChange={(e) => { setText(e.target.value); setRes(null); }} />
      <div className="mt-2 flex gap-2">
        <button type="button" className="rounded-md border border-line px-3 py-1.5 text-sm text-muted hover:bg-panel-2 disabled:opacity-50" disabled={!text} onClick={() => void run(true)}>Preview</button>
        <button type="button" className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-bg disabled:opacity-50"
          disabled={!res?.dry_run || res.errors.length > 0 || res.parsed - res.duplicates === 0} onClick={() => void run(false)}>
          Import {res?.dry_run ? res.parsed - res.duplicates : ""} trades
        </button>
      </div>
      {err && <p className="mt-2 text-sm text-down">{err}</p>}
      {res && (
        <div className="mt-2 text-xs" data-testid="journal-import-result">
          {res.errors.length > 0 ? <ul className="space-y-0.5 text-down">{res.errors.slice(0, 8).map((x) => <li key={x}>{x}</li>)}</ul> :
            res.dry_run ? <p className="text-muted">{res.parsed} trade(s) found ({res.layout}); {res.duplicates} already imported.</p> :
              <p className="text-up">Imported {res.imported} trade(s).</p>}
        </div>
      )}
      <div className="mt-3"><Callout tone="info" title="Fees">When the file has no fee column, fees are estimated from your configured commission assumptions.</Callout></div>
    </Panel>
  );
}
