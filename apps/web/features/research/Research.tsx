"use client";

import { useEffect, useMemo, useState } from "react";
import { PageHeader } from "@/components/shell/PageHeader";
import { Callout, cx, EmptyState, Panel } from "@/components/ui/primitives";
import { EquityDrawdown } from "@/components/ui/viz";
import { api } from "@/lib/api";
import { formatEt } from "@/lib/format";

type Dir = "all" | "long" | "short";
type Period = "walk_forward" | "research" | "holdout" | "all_periods";
type Sort = "win_rate" | "expectancy" | "profit_factor" | "drawdown";

interface Stats {
  trades: number; wins: number; losses: number; breakeven: number;
  win_rate: number | null; win_rate_ci95: [number, number] | null; win_rate_lower_bound: number | null;
  avg_win: number | null; avg_loss: number | null; expectancy: number | null; median_pnl: number | null;
  expectancy_ci: [number, number] | null; avg_r: number | null; profit_factor: number | null;
  gross_profit: number; gross_loss: number; net_pnl: number; max_drawdown: number;
  max_consecutive_losses: number; trades_per_session: number | null; profitable_sessions_pct: number | null;
  avg_daily_pnl: number | null; costs_pct_of_gross_profit: number | null; sharpe_daily: number | null;
  monthly: Record<string, { trades: number; net: number }>;
  equity_curve?: [string, number, number][];
}
interface Row {
  strategy_id: string; config: string; config_label: string; evidence: string; unmet_requirements: string[];
  research: Record<Dir, Stats>; walk_forward: Record<Dir, Stats>; holdout: Record<Dir, Stats>; all_periods: Record<Dir, Stats>;
  walk_forward_blocks: { block: number; first: string; last: string; trades: number; net: number }[];
  blocks_positive_pct: number | null;
  stress: Record<string, { label: string; trades: number; win_rate: number | null; expectancy: number | null; profit_factor: number | null; net_pnl: number; max_drawdown: number }>;
  apex_replay: { rules: { label: string; profit_target: number; max_drawdown: number; daily_loss_limit: number | null; daily_loss_treated_as: string }; attempts: number; resolved: number; passes: number; fails: number; pass_rate: number | null; pass_rate_ci95: [number, number] | null; median_days_to_pass: number | null; note: string };
  funnel: Record<string, number>;
  by_regime: Record<string, { trades: number; win_rate: number | null; expectancy: number | null }>;
  by_session: Record<string, { trades: number; win_rate: number | null; expectancy: number | null }>;
  walk_forward_expectancy_ci_adjusted: [number, number] | null; walk_forward_ci_level: number;
}
interface StudyResult {
  symbol: string; data_class: string; generated_at: string; comparisons: number;
  data: { bars: number; earliest_bar?: string; latest_bar?: string; usable_sessions: number; verdict: string; integrity?: Record<string, number> };
  plan: { rule: string; preliminary: boolean; sessions: number };
  rollover: { excluded_sessions: Record<string, string>; segments: { contract: string; first: string; last: string; bars: number }[]; discontinuities: { date: string; explained_by_roll: boolean }[] };
  costs: { commission_per_side: number; slippage_ticks_per_side: number };
  rows: Row[]; definitions: Record<string, string>;
}
interface Study { id: number; created_at: string; symbol: string; data_class: string; result: StudyResult; headline: { available: boolean; reason?: string; highest_win_rate?: string; strongest_expectancy?: string; preliminary?: boolean } }
interface InventoryItem { id: string; name: string; purpose: string; entry: string; stop: string; targets: string; invalidation: string; time_exit: string; source: string }
interface TradeRow { strategy_id: string; config: string; period: string; direction: string; contract: string; entry_ts: string; entry: number; stop: number; target: number; exit: number; exit_reason: string; pnl: number; r: number; mfe_usd: number; mae_usd: number; regime: string }

const EVIDENCE_CLS: Record<string, string> = {
  "Validated for paper-trading consideration": "border-up/50 text-up",
  "Promising but unvalidated": "border-warn/50 text-warn",
  "Negative evidence": "border-down/50 text-down",
  "Insufficient evidence": "border-line text-faint",
};
const pct = (x: number | null | undefined) => (x == null ? "—" : `${(x * 100).toFixed(1)}%`);
const usd = (x: number | null | undefined) => (x == null ? "—" : `${x < 0 ? "-" : ""}$${Math.abs(x).toFixed(2)}`);
const ci = (c: [number, number] | null | undefined, f: (x: number) => string) => (c ? `${f(c[0])} – ${f(c[1])}` : "—");

export function Research() {
  const [symbol, setSymbol] = useState("ES");
  const [dir, setDir] = useState<Dir>("all");
  const [period, setPeriod] = useState<Period>("walk_forward");
  const [sort, setSort] = useState<Sort>("win_rate");
  const [study, setStudy] = useState<Study | null>(null);
  const [state, setState] = useState<{ loading: boolean; error: string | null; none: boolean }>({ loading: true, error: null, none: false });
  const [selected, setSelected] = useState<string | null>(null);
  const [inventory, setInventory] = useState<InventoryItem[]>([]);

  useEffect(() => {
    void api<InventoryItem[]>("/api/research/inventory").then(setInventory).catch(() => undefined);
  }, []);
  useEffect(() => {
    const ctrl = new AbortController();
    void (async () => {
      setState({ loading: true, error: null, none: false });
      try {
        const list = await api<{ id: number }[]>(`/api/research/studies?symbol=${symbol}`, { signal: ctrl.signal });
        if (!list.length) { setStudy(null); setState({ loading: false, error: null, none: true }); return; }
        setStudy(await api<Study>(`/api/research/studies/${list[0]!.id}`, { signal: ctrl.signal }));
        setState({ loading: false, error: null, none: false });
      } catch (e) {
        if (e instanceof DOMException && e.name === "AbortError") return;
        setState({ loading: false, error: (e as Error).message, none: false });
      }
    })();
    return () => ctrl.abort();
  }, [symbol]);

  const rows = useMemo(() => {
    const rs = (study?.result.rows ?? []).filter((r) => r[period][dir].trades > 0);
    const key: Record<Sort, (r: Row) => number> = {
      win_rate: (r) => -(r[period][dir].win_rate_lower_bound ?? 0),
      expectancy: (r) => -(r[period][dir].expectancy_ci?.[0] ?? -1e9),
      profit_factor: (r) => -(r[period][dir].profit_factor ?? 0),
      drawdown: (r) => r[period][dir].max_drawdown,
    };
    return [...rs].sort((a, b) => key[sort](a) - key[sort](b));
  }, [study, period, dir, sort]);
  const sel = rows.find((r) => `${r.strategy_id}/${r.config}` === selected) ?? null;
  const res = study?.result;
  const simulated = res?.data_class === "SIMULATED";

  return (
    <>
      <PageHeader path="/research" />
      <div className="mb-3 flex flex-wrap items-center gap-2 text-xs" data-testid="research-controls">
        <Select label="Instrument" value={symbol} onChange={setSymbol} options={["ES", "NQ"]} />
        <Select label="Direction" value={dir} onChange={(v) => setDir(v as Dir)} options={["all", "long", "short"]} />
        <Select label="Period" value={period} onChange={(v) => setPeriod(v as Period)} options={["walk_forward", "research", "holdout", "all_periods"]} />
        <Select label="Sort by" value={sort} onChange={(v) => setSort(v as Sort)} options={["win_rate", "expectancy", "profit_factor", "drawdown"]} />
      </div>
      {state.error && <Callout tone="danger" title="Could not load research">{state.error}</Callout>}
      {state.loading && <p className="text-xs text-faint">Loading…</p>}
      {state.none && (
        <EmptyState title={`No research study for ${symbol} yet`}>
          Run <code className="font-mono">python -m app.cli research-study --symbols {symbol}</code> after the historical backfill has finished. Nothing is shown until a study has actually been run.
        </EmptyState>
      )}
      {study && res && (
        <div className="space-y-4">
          {simulated && <Callout tone="warn" title="SIMULATED DATA">This study ran on synthetic prices. It demonstrates the pipeline only; none of these numbers are findings.</Callout>}
          {res.plan.preliminary && !simulated && <Callout tone="warn" title="PRELIMINARY">{res.plan.rule}</Callout>}
          <Panel title={`Study #${study.id} — ${res.symbol}`}>
            <dl className="grid grid-cols-2 gap-2 text-xs sm:grid-cols-4" data-testid="research-meta">
              <div><dt className="text-faint">Data</dt><dd className="font-mono">{res.data_class} · {res.data.usable_sessions} usable sessions · {res.data.verdict}</dd></div>
              <div><dt className="text-faint">Range</dt><dd className="font-mono">{res.data.earliest_bar ? formatEt(res.data.earliest_bar, true) : "—"} → {res.data.latest_bar ? formatEt(res.data.latest_bar, true) : "—"}</dd></div>
              <div><dt className="text-faint">Contracts</dt><dd className="font-mono">{res.rollover.segments.map((s) => s.contract).join(", ") || "—"} · {Object.keys(res.rollover.excluded_sessions).length} roll sessions excluded</dd></div>
              <div><dt className="text-faint">Last run</dt><dd className="font-mono">{formatEt(res.generated_at, true)}</dd></div>
            </dl>
            <p className="mt-2 text-[11px] text-muted">{res.plan.rule}. Costs ${res.costs.commission_per_side}/side + {res.costs.slippage_ticks_per_side} tick slippage. {res.comparisons} strategy × configuration comparisons (multiple-comparison adjusted).</p>
            {study.headline.available ? (
              <ul className="mt-2 space-y-1 text-xs text-text" data-testid="research-headline">
                {study.headline.highest_win_rate && <li>{study.headline.highest_win_rate}</li>}
                {study.headline.strongest_expectancy && <li>{study.headline.strongest_expectancy}</li>}
              </ul>
            ) : <p className="mt-2 text-xs text-faint" data-testid="research-headline">No headline: {study.headline.reason}</p>}
          </Panel>

          <Panel title={`Leaderboard — ${period.replace("_", " ")}, ${dir}`}>
            <div className="overflow-x-auto">
              <table className="w-full min-w-[980px] text-left text-[11px]" data-testid="research-leaderboard">
                <thead className="text-faint"><tr>
                  <th className="py-1 pr-2">Strategy</th><th className="pr-2">Config</th><th className="pr-2">Trades</th>
                  <th className="pr-2">Win rate (95% CI)</th><th className="pr-2">$/trade (CI)</th><th className="pr-2">PF</th>
                  <th className="pr-2">Avg R</th><th className="pr-2">Max DD</th><th className="pr-2">2-tick $/trade</th><th>Evidence</th>
                </tr></thead>
                <tbody>
                  {rows.map((r) => {
                    const s = r[period][dir];
                    const k = `${r.strategy_id}/${r.config}`;
                    return (
                      <tr key={k} className={cx("cursor-pointer border-t border-line/50 hover:bg-panel-2", selected === k && "bg-panel-2")} onClick={() => setSelected(selected === k ? null : k)} data-testid={`research-row-${r.strategy_id}-${r.config}`}>
                        <td className="py-1 pr-2 font-mono">{r.strategy_id}</td>
                        <td className="pr-2 font-mono" title={r.config_label}>{r.config}</td>
                        <td className="pr-2 font-mono">{s.trades}{s.trades < 30 && <span className="ml-1 text-warn" title="Small sample: fewer than 30 trades">⚠</span>}</td>
                        <td className="pr-2 font-mono">{pct(s.win_rate)} <span className="text-faint">({ci(s.win_rate_ci95, pct)})</span></td>
                        <td className={cx("pr-2 font-mono", (s.expectancy ?? 0) > 0 ? "text-up" : "text-down")}>{usd(s.expectancy)} <span className="text-faint">({ci(s.expectancy_ci, usd)})</span></td>
                        <td className="pr-2 font-mono">{s.profit_factor ?? "—"}</td>
                        <td className="pr-2 font-mono">{s.avg_r ?? "—"}</td>
                        <td className="pr-2 font-mono">{usd(-s.max_drawdown)}</td>
                        <td className="pr-2 font-mono">{usd(r.stress.slip2?.expectancy)}</td>
                        <td><span className={cx("rounded border px-1", EVIDENCE_CLS[r.evidence])}>{r.evidence}</span></td>
                      </tr>
                    );
                  })}
                  {!rows.length && <tr><td colSpan={10} className="py-2 text-faint">No trades in this period and direction.</td></tr>}
                </tbody>
              </table>
            </div>
            <p className="mt-2 text-[10px] text-faint">Win = net P&L &gt; 0 after costs; win rate counts every trade. Win-rate sort uses the Wilson lower bound so small samples cannot top the list on luck; ⚠ = fewer than 30 trades. A high win rate is not profitability: expectancy (average net $ per trade) decides that.</p>
          </Panel>

          {sel && <Detail row={sel} study={study} period={period} dir={dir} rules={inventory.find((i) => i.id === sel.strategy_id)} />}

          <Panel title="Definitions and limitations">
            <ul className="space-y-1 text-[11px] text-muted">
              {Object.entries(res.definitions).map(([k, v]) => <li key={k}><span className="text-text">{k}:</span> {v}</li>)}
              <li>Apex replay uses the rule profile in Apex Account Health; unverified values are labelled UNVERIFIED. Past results describe the tested period only.</li>
            </ul>
          </Panel>
        </div>
      )}
    </>
  );
}

function Select({ label, value, onChange, options }: { label: string; value: string; onChange: (v: string) => void; options: string[] }) {
  return (
    <label className="flex items-center gap-1 text-muted">{label}
      <select aria-label={label} value={value} onChange={(e) => onChange(e.target.value)} className="rounded border border-line bg-panel-2 px-1 py-0.5 font-mono text-text">
        {options.map((o) => <option key={o} value={o}>{o.replace("_", " ")}</option>)}
      </select>
    </label>
  );
}

function Detail({ row, study, period, dir, rules }: { row: Row; study: Study; period: Period; dir: Dir; rules?: InventoryItem }) {
  const s = row[period][dir];
  const [trades, setTrades] = useState<TradeRow[] | null>(null);
  useEffect(() => {
    const ctrl = new AbortController();
    const q = new URLSearchParams({ strategy_id: row.strategy_id, config: row.config, limit: "200" });
    if (period !== "all_periods") q.set("period", period);
    if (dir !== "all") q.set("direction", dir);
    api<{ trades: TradeRow[] }>(`/api/research/studies/${study.id}/trades?${q}`, { signal: ctrl.signal }).then((r) => setTrades(r.trades)).catch(() => undefined);
    return () => ctrl.abort();
  }, [row, study.id, period, dir]);
  const curve = row.walk_forward.all.equity_curve?.map(([t, equity, drawdown]) => ({ t, equity, drawdown })) ?? [];
  const ap = row.apex_replay;
  return (
    <Panel title={`${row.strategy_id} — ${row.config_label}`}>
      <div className="grid gap-4 text-xs lg:grid-cols-2" data-testid="research-detail">
        <div className="space-y-2">
          <p className={cx("inline-block rounded border px-1.5", EVIDENCE_CLS[row.evidence])}>{row.evidence}</p>
          {row.unmet_requirements.length > 0 && <ul className="ml-4 list-disc text-[11px] text-muted">{row.unmet_requirements.map((u) => <li key={u}>{u}</li>)}</ul>}
          {rules && (
            <dl className="space-y-0.5 text-[11px]">
              <div><dt className="inline text-faint">Entry: </dt><dd className="inline">{rules.entry}</dd></div>
              <div><dt className="inline text-faint">Stop: </dt><dd className="inline">{rules.stop}</dd></div>
              <div><dt className="inline text-faint">Targets: </dt><dd className="inline">{rules.targets}</dd></div>
              <div><dt className="inline text-faint">Fails when: </dt><dd className="inline">{rules.invalidation}</dd></div>
              <div><dt className="inline text-faint">Source: </dt><dd className="inline font-mono">{rules.source}</dd></div>
            </dl>
          )}
          <dl className="grid grid-cols-3 gap-1 font-mono text-[11px]">
            <Stat k="Wins / losses / BE" v={`${s.wins} / ${s.losses} / ${s.breakeven}`} />
            <Stat k="Avg win / loss" v={`${usd(s.avg_win)} / ${usd(s.avg_loss)}`} />
            <Stat k="Median $/trade" v={usd(s.median_pnl)} />
            <Stat k="Net P&L" v={usd(s.net_pnl)} />
            <Stat k="Max losing streak" v={String(s.max_consecutive_losses)} />
            <Stat k="Profitable sessions" v={pct(s.profitable_sessions_pct)} />
            <Stat k="Trades/session" v={String(s.trades_per_session ?? "—")} />
            <Stat k="Costs / gross profit" v={pct(s.costs_pct_of_gross_profit)} />
            <Stat k="Daily Sharpe" v={String(s.sharpe_daily ?? "—")} />
          </dl>
          <p className="text-[11px] text-muted">Walk-forward expectancy at the multiple-comparison adjusted level ({(row.walk_forward_ci_level * 100).toFixed(2)}%): {ci(row.walk_forward_expectancy_ci_adjusted, usd)}</p>
        </div>
        <div>
          <p className="mb-1 text-[11px] text-faint">Walk-forward equity and drawdown (all directions)</p>
          <EquityDrawdown points={curve} height={200} />
        </div>
        <Mini title="Walk-forward blocks" head={["Block", "Sessions", "Trades", "Net"]} rows={row.walk_forward_blocks.map((b) => [String(b.block), `${b.first} → ${b.last}`, String(b.trades), usd(b.net)])} testId="research-blocks" />
        <Mini title="Stress tests (walk-forward)" head={["Scenario", "Trades", "Win rate", "$/trade", "PF"]} rows={Object.values(row.stress).map((x) => [x.label, String(x.trades), pct(x.win_rate), usd(x.expectancy), String(x.profit_factor ?? "—")])} testId="research-stress" />
        <div data-testid="research-apex">
          <p className="mb-1 text-[11px] font-medium text-text">Apex evaluation replay ({ap.rules.label})</p>
          <p className="text-[11px] text-muted">Target ${ap.rules.profit_target}, trailing drawdown ${ap.rules.max_drawdown}, daily loss {ap.rules.daily_loss_limit ?? "—"} ({ap.rules.daily_loss_treated_as}). Attempts {ap.attempts}, resolved {ap.resolved}: {ap.passes} passed, {ap.fails} failed — pass rate {pct(ap.pass_rate)} (95% CI {ci(ap.pass_rate_ci95, pct)}), median days to pass {ap.median_days_to_pass ?? "—"}.</p>
          <p className="text-[10px] text-faint">{ap.note}</p>
        </div>
        <Mini title="Monthly" head={["Month", "Trades", "Net"]} rows={Object.entries(s.monthly ?? {}).map(([m, v]) => [m, String(v.trades), usd(v.net)])} />
        <Mini title="By regime (walk-forward)" head={["Regime", "Trades", "Win rate", "$/trade"]} rows={Object.entries(row.by_regime).map(([k, v]) => [k, String(v.trades), pct(v.win_rate), usd(v.expectancy)])} />
        <Mini title="Signal funnel" head={["Outcome", "Count"]} rows={Object.entries(row.funnel).sort((a, b) => b[1] - a[1]).slice(0, 12).map(([k, v]) => [k, String(v)])} />
      </div>
      <div className="mt-3">
        <div className="mb-1 flex items-center justify-between">
          <p className="text-[11px] font-medium text-text">Trades (first 200)</p>
          <span className="space-x-3 text-[11px]">
            <a className="text-accent hover:underline" href={`/api/research/studies/${study.id}/export/trades.csv`}>Export all trades (CSV)</a>
            <a className="text-accent hover:underline" href={`/api/research/studies/${study.id}/export/leaderboard.csv`}>Export leaderboard (CSV)</a>
          </span>
        </div>
        <Mini head={["Entry (ET)", "Contract", "Dir", "Entry", "Stop", "Target", "Exit", "Reason", "Net", "R", "MFE", "MAE", "Period"]} rows={(trades ?? []).map((t) => [formatEt(t.entry_ts, true), t.contract, t.direction, String(t.entry), String(t.stop), String(t.target), String(t.exit), t.exit_reason, usd(t.pnl), String(t.r), usd(t.mfe_usd), usd(-t.mae_usd), t.period])} testId="research-trades" />
      </div>
    </Panel>
  );
}

function Stat({ k, v }: { k: string; v: string }) {
  return <div><dt className="font-sans text-faint">{k}</dt><dd>{v}</dd></div>;
}

function Mini({ title, head, rows, testId }: { title?: string; head: string[]; rows: string[][]; testId?: string }) {
  return (
    <div className="overflow-x-auto" data-testid={testId}>
      {title && <p className="mb-1 text-[11px] font-medium text-text">{title}</p>}
      <table className="w-full text-left text-[11px]">
        <thead className="text-faint"><tr>{head.map((h) => <th key={h} className="pr-2">{h}</th>)}</tr></thead>
        <tbody>{rows.map((r, i) => <tr key={i} className="border-t border-line/40 font-mono">{r.map((c, j) => <td key={j} className="pr-2">{c}</td>)}</tr>)}</tbody>
      </table>
      {!rows.length && <p className="text-[11px] text-faint">None.</p>}
    </div>
  );
}
