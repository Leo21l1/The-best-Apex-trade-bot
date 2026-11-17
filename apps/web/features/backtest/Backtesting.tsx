"use client";

import type { BacktestMetrics, BacktestRun, StrategiesResponse } from "@apex/shared-types";
import { useCallback, useEffect, useMemo, useState } from "react";
import { PageHeader } from "@/components/shell/PageHeader";
import { Callout, cx, EmptyState, Panel, QualityBadge, Stat, StatePill } from "@/components/ui/primitives";
import { usePolling } from "@/hooks/usePolling";
import { api, ApiError } from "@/lib/api";
import { formatEt } from "@/lib/format";
import { money } from "@/lib/money";
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
  const [form, setForm] = useState({ strategy_id: "vwap_trend", symbol: "ES", start: isoDay(-30), end: isoDay(1), slippage_ticks: "1", target_r: "" });
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
      if (selected && list.find((r) => r.id === selected.id)?.status === "done") void open(selected.id);
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
              </div>
              {error && <p role="alert" className="text-sm text-down">{error}</p>}
              <button type="submit" className="w-full rounded-md bg-accent px-3 py-2 text-sm font-medium text-bg hover:bg-accent/90">Run backtest</button>
            </form>
          </Panel>
          <Panel title="Experiments">
            {runs.length === 0 ? <p className="text-sm text-faint">None yet.</p> : (
              <ul className="space-y-1" data-testid="backtest-runs">
                {runs.map((r) => (
                  <li key={r.id}>
                    <button type="button" onClick={() => void open(r.id)}
                      className={cx("flex w-full items-center justify-between gap-2 rounded px-2 py-1.5 text-left text-xs hover:bg-panel-2", selected?.id === r.id && "bg-panel-2")}>
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
        </div>

        <div className="min-w-0">
          {!selected ? (
            <Panel><EmptyState title="Select or run an experiment" /></Panel>
          ) : selected.status !== "done" ? (
            <Panel title={`Experiment #${selected.id}`}>
              {selected.status === "failed" ? <Callout tone="danger" title="Backtest failed">{selected.error}</Callout> : (
                <p className="animate-pulse text-sm text-faint" data-testid="backtest-pending">Backtest {selected.status}… the worker processes the queue.</p>
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
          <Stat label="Profit factor" value={num(m.profit_factor)} />
          <Stat label="Expectancy" value={money(m.expectancy)} hint={`${num(m.expectancy_r, 3)} R`} />
          <Stat label="Net after costs" value={<span className={m.net_profit >= 0 ? "text-up" : "text-down"}>{money(m.net_profit)}</span>} hint={`commission ${money(m.total_commission)}`} />
          <Stat label="Max drawdown" value={money(m.max_drawdown)} hint={`${num(m.max_drawdown_r)} R`} />
          <Stat label="Avg win" value={money(m.avg_win)} hint={`avg loss ${money(m.avg_loss)}`} />
          <Stat label="Exposure" value={pct(m.exposure)} />
          <Stat label="Daily Sharpe (ann.)" value={num(m.daily_sharpe_annualized)} />
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
              <tr key={i} className="border-t border-line/60">
                <td className="py-1 pr-2 pl-4">{formatEt(t.entry_ts, true)}</td><td className={t.direction === "long" ? "text-up" : "text-down"}>{t.direction}</td>
                <td>{t.entry}</td><td>{t.stop}</td><td>{t.target}</td><td>{t.exit}</td><td className="font-sans text-muted">{t.exit_reason}</td>
                <td>{t.r.toFixed(2)}</td><td className={t.pnl >= 0 ? "text-up" : "text-down"}>{money(t.pnl)}</td>
              </tr>
            ))}</tbody>
          </table>
        </div>
        <ul className="mt-3 list-disc space-y-0.5 pl-5 text-[11px] text-faint">{r.assumptions.map((a) => <li key={a}>{a}</li>)}</ul>
      </Panel>
    </div>
  );
}
