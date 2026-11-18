"use client";

import type { BacktestMetrics, BacktestRun, BacktestTrade, CandlesResponse, SegmentRow, StrategiesResponse } from "@apex/shared-types";
import { useCallback, useEffect, useMemo, useState } from "react";
import { PageHeader } from "@/components/shell/PageHeader";
import { Callout, cx, EmptyState, Panel, QualityBadge, Stat, StatePill } from "@/components/ui/primitives";
import { usePolling } from "@/hooks/usePolling";
import { api, ApiError } from "@/lib/api";
import { formatEt } from "@/lib/format";
import { money } from "@/lib/money";
import { CandleChart, type ExtraMarker, type PriceLineSpec } from "../charts/CandleChart";
import { EquityChart } from "./EquityChart";

const input = "w-full rounded-md border border-line bg-panel-2 px-2.5 py-1.5 text-sm outline-none focus:border-accent";
const pct = (v: number | null | undefined) => (v == null ? "—" : `${(v * 100).toFixed(1)}%`);
const num = (v: number | null | undefined, d = 2) => (v == null ? "—" : Number.isFinite(v) ? v.toFixed(d) : "∞");

function isoDay(offset: number): string {
  const d = new Date(Date.now() + offset * 86_400_000);
  return d.toISOString().slice(0, 10);
}

export function Backtesting() {
  const { data: strategies } = usePolling<StrategiesResponse>("/api/strategies", 300_000);
  const [runs, setRuns] = useState<BacktestRun[]>([]);
  const [selected, setSelected] = useState<BacktestRun | null>(null);
  const [form, setForm] = useState({ strategy_id: "vwap_trend", symbol: "ES", start: isoDay(-30), end: isoDay(1), slippage_ticks: "1", target_r: "", stop_atr: "", quantity: "1", commission: "", min_net_rr: "", exit_target: "1" });
  const [compare, setCompare] = useState<number[]>([]);
  const { data: coverage } = usePolling<{ bars: number; sessions: number; first: string | null; last: string | null; enough: boolean; provider: string; note: string | null }>(
    `/api/backtests/coverage?symbol=${form.symbol}&start=${form.start}T00:00:00Z&end=${form.end}T00:00:00Z`, 60_000);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const list = await api<BacktestRun[]>("/api/backtests?limit=30");
      setRuns(list);
      return list;
    } catch (e) {
      setError((e as Error).message);
      return [];
    }
  }, []);

  useEffect(() => {
    // load() sets state only after its await resolves.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load();
  }, [load]);

  const open = useCallback(async (id: number) => setSelected(await api<BacktestRun>(`/api/backtests/${id}`)), []);

  // Poll while anything is queued or running.
  const pending = runs.some((r) => r.status === "queued" || r.status === "running");
  useEffect(() => {
    if (!pending) return;
    const t = setInterval(async () => {
      const list = await load();
      // Refresh the open experiment on ANY status change (a failed run used to keep showing "running").
      const cur = selected && list.find((r) => r.id === selected.id);
      if (cur && (cur.status !== selected.status || cur.status === "running")) void open(cur.id);
    }, 2000);
    return () => clearInterval(t);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pending, load, open, selected?.id]);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    try {
      const run = await api<BacktestRun>("/api/backtests", {
        method: "POST",
        body: {
          strategy_id: form.strategy_id, symbol: form.symbol,
          start: `${form.start}T00:00:00Z`, end: `${form.end}T00:00:00Z`,
          slippage_ticks: Number(form.slippage_ticks), target_r: form.target_r ? Number(form.target_r) : null,
          stop_atr: form.stop_atr ? Number(form.stop_atr) : null, quantity: Number(form.quantity) || 1,
          commission_per_side: form.commission ? Number(form.commission) : null,
          min_net_rr: form.min_net_rr ? Number(form.min_net_rr) : null, exit_target: Number(form.exit_target),
        },
      });
      setSelected(run);
      void load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not start backtest");
    }
  };

  const strat = strategies?.strategies.find((s) => s.id === form.strategy_id);

  return (
    <>
      <PageHeader path="/backtesting" />
      <div className="mb-4">
        <Callout tone="info" title="Same strategy code as the live scanner">
          Backtests replay stored 1-minute bars through the live strategy rules with conservative fills. Results on
          SIMULATED data are labelled and can never validate a strategy. Every experiment stores its parameters,
          dataset fingerprint and results.
        </Callout>
      </div>

      <div className="grid gap-4 lg:grid-cols-[20rem_1fr]">
        <div className="space-y-4">
          <Panel title="New experiment">
            <form onSubmit={submit} className="space-y-3">
              <label className="block text-xs text-muted">Strategy
                <select aria-label="Strategy" className={input} value={form.strategy_id}
                  onChange={(e) => setForm({ ...form, strategy_id: e.target.value })}>
                  {(strategies?.strategies ?? []).map((s) => <option key={s.id} value={s.id}>{s.code} · {s.name}</option>)}
                </select>
              </label>
              <label className="block text-xs text-muted">Instrument
                <select aria-label="Backtest instrument" className={input} value={form.symbol} onChange={(e) => setForm({ ...form, symbol: e.target.value })}>
                  {(strat?.eligible_instruments ?? ["ES"]).map((s) => <option key={s}>{s}</option>)}
                </select>
              </label>
              <div className="grid grid-cols-2 gap-2">
                <label className="text-xs text-muted">From<input type="date" aria-label="From" className={input} value={form.start} onChange={(e) => setForm({ ...form, start: e.target.value })} /></label>
                <label className="text-xs text-muted">To<input type="date" aria-label="To" className={input} value={form.end} onChange={(e) => setForm({ ...form, end: e.target.value })} /></label>
                <label className="text-xs text-muted">Slippage (ticks/side)<input aria-label="Slippage" className={input} value={form.slippage_ticks} onChange={(e) => setForm({ ...form, slippage_ticks: e.target.value })} /></label>
                <label className="text-xs text-muted">Target R (blank = spec)<input aria-label="Target R" className={input} value={form.target_r} onChange={(e) => setForm({ ...form, target_r: e.target.value })} /></label>
                <label className="text-xs text-muted">Stop × ATR (blank = spec)<input aria-label="Stop ATR multiple" className={input} value={form.stop_atr} onChange={(e) => setForm({ ...form, stop_atr: e.target.value })} /></label>
                <label className="text-xs text-muted">Contracts<input aria-label="Contracts" className={input} value={form.quantity} onChange={(e) => setForm({ ...form, quantity: e.target.value })} /></label>
                <label className="text-xs text-muted">Exit at<select aria-label="Exit target" className={input} value={form.exit_target} onChange={(e) => setForm({ ...form, exit_target: e.target.value })}><option value="1">Target 1</option><option value="2">Target 2</option></select></label>
                <label className="text-xs text-muted">Min net R:R (blank = all signals)<input aria-label="Minimum net reward to risk" className={input} value={form.min_net_rr} onChange={(e) => setForm({ ...form, min_net_rr: e.target.value })} /></label>
                <label className="col-span-2 text-xs text-muted">Commission $/side (blank = settings)<input aria-label="Commission per side" className={input} value={form.commission} onChange={(e) => setForm({ ...form, commission: e.target.value })} /></label>
              </div>
              {coverage && (
                <p className={cx("text-[11px]", coverage.enough ? "text-faint" : "text-warn")} data-testid="backtest-coverage">
                  Stored {coverage.provider} data in range: {coverage.bars.toLocaleString("en-US")} bars, {coverage.sessions} sessions
                  {coverage.first ? ` (${formatEt(coverage.first, true)} → ${formatEt(coverage.last!, true)})` : ""}. {coverage.note ?? ""}
                </p>
              )}
              {error && <p role="alert" className="text-sm text-down">{error}</p>}
              <button type="submit" className="w-full rounded-md bg-accent px-3 py-2 text-sm font-medium text-bg hover:bg-accent/90">Run backtest</button>
            </form>
          </Panel>
          <Panel title="Experiments">
            {runs.length === 0 ? <p className="text-sm text-faint">None yet.</p> : (
              <ul className="space-y-1" data-testid="backtest-runs">
                {runs.map((r) => (
                  <li key={r.id}>
                    <input type="checkbox" aria-label={`Compare #${r.id}`} className="mr-1 align-middle" disabled={r.status !== "done"}
                      checked={compare.includes(r.id)} onChange={(e) => setCompare(e.target.checked ? [...compare, r.id].slice(-4) : compare.filter((x) => x !== r.id))} />
                    <button type="button" onClick={() => void open(r.id)}
                      className={cx("inline-flex w-[calc(100%-1.5rem)] items-center justify-between gap-2 rounded px-2 py-1.5 text-left text-xs hover:bg-panel-2", selected?.id === r.id && "bg-panel-2")}>
                      <span className="font-mono">#{r.id} {r.strategy_id} {r.symbol}</span>
                      <span className={cx(r.status === "failed" ? "text-down" : r.status === "done" ? "text-muted" : "text-warn")}>
                        {r.status === "done" ? `${r.summary?.trades ?? 0} trades` : r.status}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </Panel>
          {compare.length >= 2 && <Compare ids={compare} />}
        </div>

        <div className="min-w-0">
          {!selected ? (
            <Panel><EmptyState title="Select or run an experiment" /></Panel>
          ) : selected.status !== "done" ? (
            <Panel title={`Experiment #${selected.id}`}>
              {selected.status === "failed" ? <Callout tone="danger" title="Backtest failed">{selected.error}</Callout> : (
                <div data-testid="backtest-pending" className="space-y-1 text-sm">
                  <p className="animate-pulse text-faint">Backtest {selected.status}{selected.progress ? `: ${selected.progress.phase}` : "…"}</p>
                  {selected.elapsed_seconds != null && <p className="text-xs text-faint">{selected.status === "queued" ? "Waiting" : "Running"} for {Math.floor(selected.elapsed_seconds / 60)}m {selected.elapsed_seconds % 60}s{selected.progress?.bars ? ` · ${selected.progress.bars.toLocaleString("en-US")} bars` : ""}</p>}
                  {selected.worker_note && <Callout tone="warn" title="Worker not running">{selected.worker_note}</Callout>}
                </div>
              )}
            </Panel>
          ) : selected.result ? <Result run={selected} /> : null}
        </div>
      </div>
    </>
  );
}

function MetricsRow({ label, m }: { label: string; m: BacktestMetrics }) {
  return (
    <tr className="border-b border-line/60 font-mono text-xs last:border-0">
      <td className="py-1.5 pr-3 font-sans text-muted">{label}</td>
      <td className="px-2">{m.trades}</td>
      <td className="px-2">{pct(m.win_rate)}</td>
      <td className="px-2">{num(m.profit_factor)}</td>
      <td className="px-2">{num(m.expectancy_r, 3)}</td>
      <td className="px-2">{money(m.net_profit)}</td>
      <td className="px-2">{money(m.max_drawdown)}</td>
    </tr>
  );
}

function Result({ run }: { run: BacktestRun }) {
  const r = run.result!;
  const m = r.metrics;
  const [pick, setPick] = useState<number | null>(null);
  const points = useMemo(
    () => r.trades.reduce<{ t: number; v: number }[]>((acc, t) => {
      const prev = acc.length ? acc[acc.length - 1]!.v : 0;
      acc.push({ t: Math.floor(new Date(t.exit_ts).getTime() / 1000), v: prev + t.pnl });
      return acc;
    }, []),
    [r.trades],
  );
  return (
    <div className="space-y-4" data-testid="backtest-result">
      <Panel
        title={`Experiment #${run.id} · ${run.strategy_id} · ${run.symbol}`}
        actions={<span className="flex items-center gap-2"><QualityBadge quality={r.dataset.quality} /><StatePill state={r.validation.passed ? "ok" : "degraded"} label={r.validation.passed ? "Meets standards" : "Not validated"} /></span>}
      >
        {r.dataset.quality === "simulated" && (
          <div className="mb-3"><Callout tone="warn" title="SIMULATED DATA">These results come from synthetic prices. They say nothing about real-market performance.</Callout></div>
        )}
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-4 lg:grid-cols-6">
          <Stat label="Trades" value={m.trades} hint={`${num(m.trades_per_day)} / day`} />
          <Stat label="Win rate" value={pct(m.win_rate)} />
          <Stat label="Profit factor" value={m.no_losing_trades ? "∞" : num(m.profit_factor)} hint={m.no_losing_trades ? "no losing trades" : undefined} />
          <Stat label="Expectancy" value={money(m.expectancy)} hint={`${num(m.expectancy_r, 3)} R`} />
          <Stat label="Net after costs" value={<span className={m.net_profit >= 0 ? "text-up" : "text-down"}>{money(m.net_profit)}</span>} hint={`commission ${money(m.total_commission)}`} />
          <Stat label="Max drawdown" value={money(m.max_drawdown)} hint={`${num(m.max_drawdown_r)} R`} />
          <Stat label="Avg win" value={money(m.avg_win)} hint={`avg loss ${money(m.avg_loss)}`} />
          <Stat label="Exposure" value={pct(m.exposure)} />
          <Stat label="Daily Sharpe (ann.)" value={num(m.daily_sharpe_annualized)} />
          <Stat label="Avg hold" value={m.avg_hold_minutes != null ? `${m.avg_hold_minutes} min` : "—"} />
          <Stat label="Streaks" value={`${m.max_consecutive_wins ?? "—"}W / ${m.max_consecutive_losses ?? "—"}L`} hint="max consecutive" />
          <Stat label="Exits" value={m.trades} hint={Object.entries(m.exit_reasons).filter(([, n]) => n).map(([k, n]) => `${k} ${n}`).join(" · ") || "—"} />
        </div>
        <div className="mt-4"><EquityChart points={points} /></div>
        <p className="mt-2 text-[11px] text-faint">
          Dataset: {r.dataset.bars.toLocaleString("en-US")} bars, {r.dataset.trading_days} trading days, {r.dataset.first ? formatEt(r.dataset.first, true) : ""} → {r.dataset.last ? formatEt(r.dataset.last, true) : ""} · sha256 {r.dataset.sha256.slice(0, 12)}… · engine {r.engine_version} · result {r.result_sha256.slice(0, 12)}…
        </p>
      </Panel>

      <div className="grid gap-4 xl:grid-cols-2">
        <Panel title="Validation against research standards">
          <ul className="space-y-1 text-sm" data-testid="validation-checks">
            {r.validation.checks.map((c) => (
              <li key={c.name} className="flex gap-2"><span className={c.passed ? "text-up" : "text-down"}>{c.passed ? "✓" : "✗"}</span><span>{c.name} <span className="text-faint">({c.detail})</span></span></li>
            ))}
          </ul>
          <p className="mt-3 text-xs text-muted">
            Multiple testing: {r.multiple_testing.configurations_tested} configurations tried for this strategy. t-stat of mean R {num(r.multiple_testing.t_stat_r)} vs {num(r.multiple_testing.z_required_bonferroni)} required (Bonferroni) →{" "}
            {r.multiple_testing.significant_after_adjustment ? "significant" : "not significant"}.
          </p>
        </Panel>
        <Panel title="In-sample vs out-of-sample and walk-forward" className="xl:col-span-2">
          <div className="-mx-4 overflow-x-auto">
            <table className="w-full min-w-[480px]">
              <thead><tr className="text-left text-[11px] text-faint">{["", "Trades", "Win", "PF", "Exp R", "Net", "Max DD"].map((h) => <th key={h} className="px-2 py-1 font-medium first:pl-4">{h}</th>)}</tr></thead>
              <tbody className="[&_td:first-child]:pl-4">
                <MetricsRow label={`In-sample (${r.split.in_sample_days}d)`} m={r.in_sample} />
                <MetricsRow label={`Out-of-sample (${r.split.out_of_sample_days}d)`} m={r.out_of_sample} />
                {r.walk_forward.map((f) => <MetricsRow key={f.fold} label={`Fold ${f.fold} (${f.first_day.slice(5)}→${f.last_day.slice(5)})`} m={f.metrics} />)}
              </tbody>
            </table>
          </div>
        </Panel>
        <Panel title="Monte Carlo (bootstrap of trade P&L)">
          {r.monte_carlo ? (
            <dl className="grid grid-cols-2 gap-x-6 gap-y-1 text-sm">
              <dt className="text-muted">Net profit 5th / 50th / 95th</dt><dd className="font-mono">{money(r.monte_carlo.net_profit.p5)} / {money(r.monte_carlo.net_profit.p50)} / {money(r.monte_carlo.net_profit.p95)}</dd>
              <dt className="text-muted">Max drawdown 50th / 95th / 99th</dt><dd className="font-mono">{money(r.monte_carlo.max_drawdown.p50)} / {money(r.monte_carlo.max_drawdown.p95)} / {money(r.monte_carlo.max_drawdown.p99)}</dd>
              <dt className="text-muted">Probability of a net loss</dt><dd className="font-mono">{pct(r.monte_carlo.probability_of_loss)}</dd>
              <dt className="text-muted">Runs / seed</dt><dd className="font-mono">{r.monte_carlo.runs} / {r.monte_carlo.seed}</dd>
            </dl>
          ) : <p className="text-sm text-faint">Needs at least 10 trades.</p>}
        </Panel>
        <Panel title="Parameter sensitivity">
          {r.sensitivity.length === 0 ? <p className="text-sm text-faint">Not run (no trades in the base run).</p> : (
            <table className="w-full text-xs">
              <thead><tr className="text-left text-faint">{["Variant", "Trades", "PF", "Exp R", "Net"].map((h) => <th key={h} className="py-1 font-medium">{h}</th>)}</tr></thead>
              <tbody className="font-mono">{r.sensitivity.map((v) => (
                <tr key={v.variant} className="border-t border-line/60"><td className="py-1 font-sans text-muted">{v.variant}</td><td>{v.trades}</td><td>{num(v.profit_factor)}</td><td>{num(v.expectancy_r, 3)}</td><td>{money(v.net_profit)}</td></tr>
              ))}</tbody>
            </table>
          )}
        </Panel>
      </div>

      <Panel title={`Trades (${r.trades.length})`}>
        <div className="-mx-4 max-h-80 overflow-auto">
          <table className="w-full min-w-[720px] text-xs">
            <thead className="sticky top-0 bg-panel"><tr className="text-left text-faint">{["Entry (ET)", "Side", "Entry", "Stop", "Target", "Exit", "Reason", "R", "Net"].map((h) => <th key={h} className="px-2 py-1 font-medium first:pl-4">{h}</th>)}</tr></thead>
            <tbody className="font-mono">{r.trades.map((t, i) => (
              <tr key={i} className={cx("cursor-pointer border-t border-line/60 hover:bg-panel-2/40", pick === i && "bg-accent/10")} onClick={() => setPick(pick === i ? null : i)} data-testid={`bt-trade-${i}`}>
                <td className="py-1 pr-2 pl-4">{formatEt(t.entry_ts, true)}</td><td className={t.direction === "long" ? "text-up" : "text-down"}>{t.direction}</td>
                <td>{t.entry}</td><td>{t.stop}</td><td>{t.target}</td><td>{t.exit}</td><td className="font-sans text-muted">{t.exit_reason}</td>
                <td>{t.r.toFixed(2)}</td><td className={t.pnl >= 0 ? "text-up" : "text-down"}>{money(t.pnl)}</td>
              </tr>
            ))}</tbody>
          </table>
        </div>
        {pick != null && r.trades[pick] && <TradeReplay symbol={run.symbol} t={r.trades[pick]!} />}
        <ul className="mt-3 list-disc space-y-0.5 pl-5 text-[11px] text-faint">{r.assumptions.map((a) => <li key={a}>{a}</li>)}</ul>
      </Panel>
      {r.segments && <Segments seg={r.segments} />}
    </div>
  );
}


/** Segment statistics, in-sample and out-of-sample side by side. */
function Segments({ seg }: { seg: NonNullable<BacktestRun["result"]>["segments"] }) {
  const [dim, setDim] = useState("session");
  if (!seg) return null;
  const dims = Object.keys(seg.in_sample);
  const table = (rows: SegmentRow[] | undefined, label: string) => (
    <div className="min-w-0">
      <p className="mb-1 text-[11px] font-medium text-muted uppercase">{label}</p>
      {!rows?.length ? <p className="text-xs text-faint">No trades.</p> : (
        <table className="w-full text-xs"><thead><tr className="text-left text-faint">{["Segment", "n", "Win", "PF", "Exp R", "Net", "Avg W/L"].map((h) => <th key={h} className="pr-2 font-medium">{h}</th>)}</tr></thead>
          <tbody className="font-mono">{rows.map((x) => (
            <tr key={x.key} className={cx("border-t border-line/60", x.small_sample && "text-faint")} title={x.small_sample ? "fewer than 30 trades" : undefined}>
              <td className="pr-2 font-sans">{x.key}{x.small_sample ? " *" : ""}</td><td>{x.trades}</td><td>{pct(x.win_rate)}</td><td>{num(x.profit_factor)}</td>
              <td>{num(x.expectancy_r, 3)}</td><td className={x.net_profit >= 0 ? "text-up" : "text-down"}>{money(x.net_profit)}</td>
              <td>{money(x.avg_win)} / {money(x.avg_loss)}</td>
            </tr>))}</tbody></table>
      )}
    </div>
  );
  return (
    <Panel title="Results by segment" actions={
      <select aria-label="Segment dimension" value={dim} onChange={(e) => setDim(e.target.value)} className="rounded border border-line bg-panel-2 px-1 py-0.5 text-xs">
        {dims.map((d) => <option key={d} value={d}>{d.replaceAll("_", " ")}</option>)}
      </select>}>
      <div className="grid gap-4 lg:grid-cols-2" data-testid="bt-segments">{table(seg.in_sample[dim], "In-sample")}{table(seg.out_of_sample[dim], "Out-of-sample")}</div>
      <p className="mt-2 text-[11px] text-faint">* fewer than 30 trades. {seg.note}</p>
    </Panel>
  );
}

const TICKS: Record<string, number> = { CL: 0.01, MCL: 0.01, GC: 0.1, MGC: 0.1, YM: 1, MYM: 1, RTY: 0.1, M2K: 0.1 };

/** Historical chart around one simulated trade, with entry/exit markers and stop/target lines. */
function TradeReplay({ symbol, t }: { symbol: string; t: BacktestTrade }) {
  const before = new Date(new Date(t.exit_ts).getTime() + 30 * 60_000).toISOString();
  const { data } = usePolling<CandlesResponse>(`/api/market/candles?symbol=${symbol}&timeframe=1m&limit=150&before=${encodeURIComponent(before)}`, 3_600_000);
  const toS = (x: string) => Math.floor(new Date(x).getTime() / 1000 / 60) * 60;
  const long = t.direction === "long";
  const markers: ExtraMarker[] = [
    { t: toS(t.entry_ts), text: `${long ? "BUY" : "SELL"} ${t.entry}`, color: long ? "#34d399" : "#f05a5e", position: long ? "belowBar" : "aboveBar", shape: long ? "arrowUp" : "arrowDown" },
    { t: toS(t.exit_ts), text: `EXIT ${t.exit} (${t.exit_reason})`, color: "#f5b84a", position: long ? "aboveBar" : "belowBar", shape: "circle" },
  ];
  const lines: PriceLineSpec[] = [
    { price: t.stop, title: "STOP", color: "#f05a5e", dashed: true },
    { price: t.target, title: "TARGET", color: "#34d399", dashed: true },
  ];
  return (
    <div className="mt-3 rounded border border-line/60 p-2" data-testid="bt-trade-replay">
      <p className="mb-1 text-xs text-muted">Simulated {t.direction} {formatEt(t.entry_ts, true)} → {formatEt(t.exit_ts, true)} · {t.session ?? ""} · regime {t.regime ?? "?"} · R {t.r.toFixed(2)}</p>
      {!data?.bars.length ? <p className="text-xs text-faint">No stored bars around this trade.</p> : (
        <CandleChart bars={data.bars} tick={TICKS[symbol] ?? 0.25} daily={false} seriesKey={`bt-${t.entry_ts}`} lines={lines} markers={markers} height="h-72 w-full" />
      )}
    </div>
  );
}

/** Side-by-side metrics of 2-4 completed experiments. */
interface CompareRow { id: number; strategy_id: string; symbol: string; start: string; end: string; params: Record<string, unknown>; metrics: BacktestMetrics | null; out_of_sample: BacktestMetrics | null }

function Compare({ ids }: { ids: number[] }) {
  const { data } = usePolling<CompareRow[]>(`/api/backtests/compare?ids=${ids.join(",")}`, 600_000);
  if (!data) return null;
  const rows: [string, (x: CompareRow) => string][] = [
    ["Strategy", (x) => `${x.strategy_id} ${x.symbol}`], ["Range", (x) => `${x.start.slice(0, 10)} → ${x.end.slice(0, 10)}`],
    ["Trades", (x) => String(x.metrics?.trades ?? "—")], ["Win rate", (x) => pct(x.metrics?.win_rate ?? null)],
    ["Profit factor", (x) => num(x.metrics?.profit_factor ?? null)], ["Expectancy R", (x) => num(x.metrics?.expectancy_r ?? null, 3)],
    ["Net", (x) => money(x.metrics?.net_profit ?? null)], ["Max DD", (x) => money(x.metrics?.max_drawdown ?? null)],
    ["OOS exp. R", (x) => num(x.out_of_sample?.expectancy_r ?? null, 3)], ["OOS net", (x) => money(x.out_of_sample?.net_profit ?? null)],
  ];
  return (
    <Panel title="Compare experiments">
      <table className="w-full text-xs" data-testid="bt-compare"><thead><tr><th />{data.map((x) => <th key={x.id} className="text-left font-mono">#{x.id}</th>)}</tr></thead>
        <tbody>{rows.map(([label, f]) => <tr key={label} className="border-t border-line/60"><td className="pr-2 text-faint">{label}</td>{data.map((x) => <td key={x.id} className="font-mono">{f(x)}</td>)}</tr>)}</tbody>
      </table>
    </Panel>
  );
}
