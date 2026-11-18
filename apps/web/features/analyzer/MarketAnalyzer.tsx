"use client";

import type { Timeframe } from "@apex/shared-types";
import { useMemo, useState } from "react";
import { PageHeader } from "@/components/shell/PageHeader";
import { cx, Panel } from "@/components/ui/primitives";
import { CandleChart } from "@/features/charts/CandleChart";
import { useCandles } from "@/features/charts/useCandles";
import { usePolling } from "@/hooks/usePolling";
import {
  ageText, analyzerShapes, BIAS_TONE, DEFAULT_OVERLAYS, evidenceText, fmtPrice, headline, NEWS_TONE, STRATEGY_NAME,
  type Analysis, type Card, type Factor, type Level, type OverlayOptions, type Overview,
} from "@/lib/analyzer";
import { formatEt } from "@/lib/format";

const STATUS_TONE: Record<string, string> = {
  Confirmed: "text-up", Developing: "text-accent", Conflicting: "text-warn", Invalidated: "text-down", "Not applicable": "text-faint",
};

function DataPill({ d }: { d: Card["data"] }) {
  const tone = d.label === "LIVE" ? "text-up border-up/40" : d.label === "SIMULATED" ? "text-accent border-accent/40" : d.label === "MARKET CLOSED" || d.label === "HISTORICAL" || d.label === "DELAYED" ? "text-muted border-line" : "text-warn border-warn/50";
  return <span className={cx("rounded border px-1.5 py-0.5 font-mono text-[10px] tracking-wider", tone)} data-testid="data-label">{d.label}</span>;
}

function LevelLine({ lv, tick }: { lv: Level | null; tick: number }) {
  if (!lv) return <span className="text-faint">—</span>;
  return <span><span className="font-mono">{fmtPrice(lv.price, tick)}</span> <span className="text-faint">{lv.short ?? lv.label}</span></span>;
}

function InstrumentCard({ c, active, onOpen }: { c: Card; active: boolean; onOpen: () => void }) {
  return (
    <button type="button" onClick={onOpen} data-testid={`card-${c.symbol}`} aria-pressed={active}
      className={cx("w-full rounded-lg border bg-panel p-4 text-left transition hover:border-accent/60", active ? "border-accent" : "border-line")}>
      <div className="flex items-start justify-between gap-2">
        <div>
          <p className="text-lg font-semibold tracking-tight">{c.symbol}</p>
          <p className="text-[11px] text-faint">{c.name}</p>
        </div>
        <span className={cx("rounded border px-2 py-1 text-sm font-bold tracking-wider", headline(c.state).tone)} data-testid="card-state">{headline(c.state).text}</span>
      </div>
      <div className="mt-3 flex items-baseline gap-2">
        <span className="font-mono text-2xl" data-testid="price">{c.data.price == null ? "—" : fmtPrice(c.data.price)}</span>
        <DataPill d={c.data} />
        {c.data.confidence === "LOW CONFIDENCE" && <span className="rounded border border-warn/50 px-1.5 py-0.5 font-mono text-[10px] text-warn" title={c.data.confidence_note}>LOW CONFIDENCE</span>}
      </div>
      <p className="text-[11px] text-faint">
        {c.data.price_time ? `${formatEt(c.data.price_time)} ET · ${ageText(c.data.age_seconds)}` : c.data.note ?? "No data"}
        {c.data.contract ? ` · ${c.data.contract}` : ""}
      </p>
      <dl className="mt-3 space-y-1 text-xs">
        <div className="flex justify-between gap-2"><dt className="text-faint">Direction</dt><dd><span className={cx("rounded border px-1.5 text-[11px] font-semibold", BIAS_TONE[c.bias.direction])} data-testid="bias">{c.bias.label}</span></dd></div>
        <div className="flex justify-between gap-2"><dt className="text-faint">Confluence</dt><dd className="font-mono" data-testid="card-confluence">{c.confluence?.score == null ? "—" : `${c.confluence.score}/5`}</dd></div>
        <div className="flex justify-between gap-2"><dt className="text-faint">Last confirmation</dt><dd className="font-mono">{c.last_confirmation ?? "—"}</dd></div>
        <div className="flex justify-between gap-2"><dt className="text-faint">Session</dt><dd>{c.session.name}</dd></div>
        <div className="flex justify-between gap-2"><dt className="text-faint">Structure</dt><dd className="truncate">{c.headline_structure ?? "—"}</dd></div>
        <div className="flex justify-between gap-2"><dt className="text-faint">Liquidity above</dt><dd><LevelLine lv={c.liquidity.above} tick={0.25} /></dd></div>
        <div className="flex justify-between gap-2"><dt className="text-faint">Liquidity below</dt><dd><LevelLine lv={c.liquidity.below} tick={0.25} /></dd></div>
      </dl>
      <p className={cx("mt-3 text-[11px] font-semibold", NEWS_TONE[c.news.status ?? ""] ?? "text-faint")} title={c.news.summary ?? ""}>
        {c.news.status ?? "News status unknown"}
      </p>
    </button>
  );
}

function Section({ letter, title, children }: { letter: string; title: string; children: React.ReactNode }) {
  return (
    <Panel title={`${letter}. ${title}`}>
      <div className="text-sm">{children}</div>
    </Panel>
  );
}

function AnalyzerChart({ a }: { a: Analysis }) {
  const [tf, setTf] = useState<Timeframe>("5m");
  const [o, setO] = useState<OverlayOptions>(DEFAULT_OVERLAYS);
  const { bars } = useCandles(a.symbol, tf);
  const times = useMemo(() => bars.map((b) => b.t), [bars]);
  const smc = useMemo(() => analyzerShapes(a, tf, times, o), [a, tf, times, o]);
  const toggle = (k: keyof OverlayOptions) => setO((p) => ({ ...p, [k]: !p[k] }));
  return (
    <Panel title="Chart" actions={
      <div className="flex flex-wrap items-center gap-1 text-[11px]">
        {(["1m", "5m", "15m", "1h"] as Timeframe[]).map((t) => (
          <button key={t} type="button" onClick={() => setTf(t)} className={cx("rounded px-2 py-0.5 font-mono", tf === t ? "bg-panel-2 text-text" : "text-muted hover:text-text")}>{t}</button>
        ))}
      </div>
    }>
      <div className="mb-2 flex flex-wrap gap-3 text-[11px] text-muted" data-testid="overlay-toggles">
        {([["fvg", "FVGs"], ["ob", "Order blocks"], ["liquidity", "Liquidity"], ["structure", "BOS / CHoCH"], ["session", "Session levels"]] as [keyof OverlayOptions, string][]).map(([k, label]) => (
          <label key={k} className="flex items-center gap-1"><input type="checkbox" checked={o[k]} onChange={() => toggle(k)} />{label}</label>
        ))}
      </div>
      {bars.length ? (
        <CandleChart bars={bars} tick={a.tick_size} daily={false} seriesKey={`${a.symbol}:${tf}`} height="h-[420px] w-full"
          toggles={{ ema: false, vwap: true, levels: false, signals: false, news: false }} smc={smc} />
      ) : <p className="py-16 text-center text-sm text-faint">No chart data for {a.symbol}.</p>}
      <p className="mt-2 text-[11px] text-faint">Only the nearest active zones are drawn. Zones and levels are candle-geometry heuristics.</p>
    </Panel>
  );
}

function Detail({ symbol }: { symbol: string }) {
  const { data: a, error } = usePolling<Analysis>(`/api/analyzer/${symbol}`, 20_000);
  if (error && !a) return <Panel><p className="text-sm text-down">{error}</p></Panel>;
  if (!a) return <Panel><p className="text-sm text-faint">Loading {symbol} analysis…</p></Panel>;
  const tick = a.tick_size;
  const keyLevels = a.levels.filter((l) => l.valid).sort((x, y) => Math.abs(x.distance) - Math.abs(y.distance)).slice(0, 12)
    .sort((x, y) => y.price - x.price);
  return (
    <div className="space-y-4" data-testid="analysis-detail">
      <SignalPanel a={a} />
      <Section letter="A" title="Market bias">
        <div className="flex flex-wrap items-center gap-3">
          <span className={cx("rounded border px-2.5 py-1 text-sm font-semibold tracking-wider", BIAS_TONE[a.bias.direction])}>{a.bias.label}</span>
          <p className="text-base" data-testid="bias-sentence">{a.bias.sentence}</p>
        </div>
        {a.bias.pullback && <p className="mt-2 text-xs text-muted">The short-term 5-minute structure ({a.bias.short_term_5m}) differs from the broader bias: a pullback, not a contradiction.</p>}
        <p className="mt-2 text-xs text-faint">{a.narrative}</p>
      </Section>
      <div className="grid gap-4 lg:grid-cols-2">
        <Section letter="B" title="What's happening now">
          {a.observations.length ? (
            <ul className="list-disc space-y-1 pl-5" data-testid="observations">{a.observations.map((o) => <li key={o}>{o}</li>)}</ul>
          ) : <p className="text-faint">No notable structure events, sweeps or zone retests right now.</p>}
        </Section>
        <Section letter="E" title={a.why_wait.length ? "Why wait?" : "Notes"}>
          {a.why_wait.length ? (
            <ul className="list-disc space-y-1 pl-5" data-testid="why-wait">{a.why_wait.map((w) => <li key={w}>{w}</li>)}</ul>
          ) : <p className="text-faint">All technical requirements are met; see the setup above.</p>}
        </Section>
      </div>
      <Section letter="C" title="Important price levels">
        {keyLevels.length ? (
          <div className="-mx-4 overflow-x-auto">
            <table className="w-full min-w-[560px] text-xs" data-testid="levels">
              <thead><tr className="text-left text-faint">{["Level", "Price", "TF", "Status", "Distance"].map((h) => <th key={h} className="px-2 py-1 font-medium first:pl-4">{h}</th>)}</tr></thead>
              <tbody>
                {keyLevels.map((l) => (
                  <tr key={`${l.label}-${l.price}`} className="border-t border-line/40">
                    <td className="px-2 py-1 pl-4">{l.label}</td>
                    <td className="px-2 py-1 font-mono">{l.top != null && l.bottom != null ? `${fmtPrice(l.bottom, tick)}–${fmtPrice(l.top, tick)}` : fmtPrice(l.price, tick)}</td>
                    <td className="px-2 py-1 font-mono">{l.timeframe}</td>
                    <td className="px-2 py-1">{l.state}</td>
                    <td className={cx("px-2 py-1 font-mono", l.distance > 0 ? "text-down/80" : "text-up/80")}>{l.distance > 0 ? "+" : ""}{fmtPrice(l.distance, tick)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : <p className="text-faint">No valid levels detected from the available data.</p>}
      </Section>
      <AnalyzerChart a={a} />
      <details className="rounded-lg border border-line bg-panel p-4" data-testid="advanced">
        <summary className="cursor-pointer text-sm font-semibold">Advanced analysis</summary>
        <div className="mt-4 grid gap-4 lg:grid-cols-2">
          <div>
            <p className="mb-2 text-xs font-semibold text-muted">Both directions ({a.confluence?.label})</p>
            <p className="mb-2 font-mono text-xs">BUY {a.signal.direction_scores?.bullish ?? "—"}/5 · SELL {a.signal.direction_scores?.bearish ?? "—"}/5 · threshold {a.confluence?.threshold ?? 4}/5</p>
            <FactorList fs={a.signal.factors} testId="five-factors" />
            <p className="mb-1 mt-3 text-xs font-semibold text-muted">Strategy signals evaluated now</p>
            <ul className="space-y-1 text-xs" data-testid="candidates">
              {a.signal.candidates.length ? a.signal.candidates.map((c) => (
                <li key={`${c.strategy_id}-${c.trigger_at}`}>{STRATEGY_NAME[c.strategy_id] ?? c.strategy_id} {c.direction} — {c.score}/5{c.confirmed ? " · confirmed" : ` · missing: ${c.missing.slice(0, 2).join("; ")}`}</li>
              )) : <li className="text-faint">No strategy has triggered in the last 30 minutes.</li>}
            </ul>
            <ForwardStats />
          </div>
          <div>
            <p className="mb-2 text-xs font-semibold text-muted">Strategies by family</p>
            {Object.entries(a.strategies).map(([fam, rows]) => (
              <div key={fam} className="mb-3">
                <p className="text-[11px] tracking-wider text-faint uppercase">{fam}</p>
                <ul className="text-xs">
                  {rows.map((r) => <li key={r.id} className="flex justify-between gap-2"><span>{r.name}</span><span className={STATUS_TONE[r.status]} title={r.detail}>{r.status}</span></li>)}
                </ul>
              </div>
            ))}
          </div>
          <div className="lg:col-span-2">
            <p className="mb-2 text-xs font-semibold text-muted">Timeframes</p>
            <table className="w-full text-xs">
              <thead><tr className="text-left text-faint"><th>TF</th><th>Internal</th><th>Swing</th><th>Last swing event</th><th>Dealing range</th></tr></thead>
              <tbody>
                {Object.entries(a.timeframes).map(([k, t]) => (
                  <tr key={k} className="border-t border-line/40">
                    <td className="py-1 font-mono">{k}</td><td>{t.internal_trend ?? "—"}</td><td>{t.swing_trend ?? "—"}</td>
                    <td>{t.last_swing ? `${t.last_swing.direction} ${t.last_swing.kind} ${fmtPrice(t.last_swing.level, tick)}` : "—"}</td>
                    <td>{t.premium_discount ? `${t.premium_discount.zone} (${Math.round(t.premium_discount.position * 100)}%)` : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </details>
      <p className="text-[11px] text-faint">{a.disclaimer}</p>
    </div>
  );
}

const FACTOR_TONE: Record<string, string> = { PASS: "text-up", FAIL: "text-down", UNKNOWN: "text-warn" };

function FactorList({ fs, testId }: { fs: Factor[]; testId?: string }) {
  return (
    <ul className="space-y-1 text-xs" data-testid={testId}>
      {fs.map((f) => (
        <li key={f.n} className="flex gap-2"><span className={cx("w-16 shrink-0 font-mono font-semibold", FACTOR_TONE[f.status])}>{f.status}</span><span><span className="text-muted">{f.n}. {f.name}:</span> {f.detail}</span></li>
      ))}
    </ul>
  );
}

/** The conclusion first: technical state, levels, timing, evidence; then the five confirmations. */
function SignalPanel({ a }: { a: Analysis }) {
  const sig = a.signal;
  const rec = sig.recorded ?? null;
  const state = sig.display_state ?? sig.state;
  const h = headline(state);
  const best = sig.best;
  const tick = a.tick_size;
  const lv = rec
    ? { entry: rec.entry, zone: rec.entry_zone, stop: rec.stop, tp1: rec.tp1, tp2: rec.tp2, rr1: rec.net_rr_tp1, rr2: rec.rr_tp2?.net ?? null,
        stopBasis: rec.stop_basis, tp1Basis: rec.tp1_basis, tp2Basis: rec.tp2_basis }
    : best ? { entry: best.levels.entry, zone: best.levels.entry_zone, stop: best.levels.stop, tp1: best.levels.tp1, tp2: best.levels.tp2,
        rr1: best.levels.rr_tp1?.net ?? null, rr2: best.levels.rr_tp2?.net ?? null, stopBasis: best.levels.stop_basis,
        tp1Basis: best.levels.tp1_basis, tp2Basis: best.levels.tp2_basis } : null;
  const factors = rec?.factors?.length ? rec.factors : sig.factors;
  const score = factors.filter((f) => f.status === "PASS").length;
  const ev = evidenceText(rec?.evidence ?? best?.evidence);
  const confirmed = state.includes("CONFIRMED");
  const strategy = rec?.strategy_id ?? best?.strategy_id;
  const missing = [...factors.filter((f) => f.status !== "PASS").map((f) => `${f.name}: ${f.detail}`),
    ...(!rec && best && !best.confirmed ? best.missing.filter((m) => !m.includes("confluences")) : [])];
  return (
    <section className={cx("rounded-lg border bg-panel p-4", confirmed ? (state.includes("BUY") ? "border-up/60" : "border-down/60") : "border-line")} data-testid="signal-panel">
      <div className="flex flex-wrap items-baseline gap-3">
        <h2 className="text-lg font-bold tracking-wide"><span>{a.symbol}</span> — <span className={cx(h.tone.split(" ")[0])} data-testid="signal-state">{state}</span></h2>
        <span className="font-mono text-sm" data-testid="signal-confluence">Confluence {score}/5</span>
        {(rec || best) && <span className={cx("rounded border px-2 py-0.5 text-[11px] font-semibold", ev.warn ? "border-warn/50 text-warn" : "border-up/50 text-up")} data-testid="evidence">Historical evidence: {ev.text}</span>}
        {a.data.label !== "LIVE" && <span className="rounded border border-warn/50 px-2 py-0.5 text-[11px] text-warn">DATA {a.data.label}</span>}
      </div>
      {rec?.status_reason && <p className="mt-1 text-xs text-warn">{rec.status_reason}</p>}
      {sig.note && !best && <p className="mt-1 text-xs text-muted">{sig.note}</p>}
      {lv && (
        <dl className="mt-3 grid grid-cols-2 gap-x-6 gap-y-1 font-mono text-sm sm:grid-cols-4" data-testid="signal-levels">
          <div><dt className="text-[11px] text-faint">ENTRY</dt><dd>{fmtPrice(lv.entry, tick)} <span className="text-[11px] text-faint">({fmtPrice(lv.zone[0], tick)}–{fmtPrice(lv.zone[1], tick)})</span></dd></div>
          <div><dt className="text-[11px] text-faint">STOP LOSS</dt><dd className="text-down" title={lv.stopBasis ?? ""}>{fmtPrice(lv.stop, tick)}</dd></div>
          <div><dt className="text-[11px] text-faint">TP1</dt><dd className="text-up" title={lv.tp1Basis ?? ""}>{fmtPrice(lv.tp1, tick)}</dd></div>
          <div><dt className="text-[11px] text-faint">TP2</dt><dd className="text-up" title={lv.tp2Basis ?? ""}>{fmtPrice(lv.tp2, tick)}</dd></div>
          <div><dt className="text-[11px] text-faint">NET R:R TP1 / TP2</dt><dd>{lv.rr1 ?? "—"} / {lv.rr2 ?? "—"}</dd></div>
          <div><dt className="text-[11px] text-faint">CONFIRMED</dt><dd>{rec ? rec.confirmed_at_et : "—"}</dd></div>
          <div><dt className="text-[11px] text-faint">EXPIRES</dt><dd>{rec ? rec.expires_at_et : "—"}</dd></div>
          <div><dt className="text-[11px] text-faint">STRATEGY</dt><dd className="font-sans">{strategy ? STRATEGY_NAME[strategy] ?? strategy : "—"}</dd></div>
        </dl>
      )}
      {lv && (
        <p className="mt-1 text-[11px] text-faint">Stop: {lv.stopBasis ?? "—"} · TP1: {lv.tp1Basis ?? "—"}{lv.tp2Basis ? ` · TP2: ${lv.tp2Basis}` : ""}. Analytical levels, not orders or guaranteed fills.</p>
      )}
      <div className="mt-3 grid gap-3 md:grid-cols-2">
        <div>
          <p className="mb-1 text-xs font-semibold text-up">Confirmations</p>
          <ul className="list-disc space-y-0.5 pl-5 text-xs" data-testid="confirmations">
            {factors.filter((f) => f.status === "PASS").map((f) => <li key={f.n}>{f.name}: {f.detail}</li>)}
            {!factors.some((f) => f.status === "PASS") && <li className="list-none text-faint">None yet.</li>}
          </ul>
        </div>
        <div>
          <p className="mb-1 text-xs font-semibold text-warn">Missing</p>
          <ul className="list-disc space-y-0.5 pl-5 text-xs" data-testid="missing">
            {missing.slice(0, 6).map((m) => <li key={m}>{m}</li>)}
            {!missing.length && <li className="list-none text-faint">Nothing missing.</li>}
          </ul>
        </div>
      </div>
      <p className={cx("mt-3 text-xs font-semibold", NEWS_TONE[a.news?.status ?? ""] ?? "text-faint")}>News: {a.news?.status ?? "unknown"} — <span className="font-normal text-muted">{a.news?.summary}</span></p>
      <p className="mt-2 text-[11px] text-faint">
        A technical confirmation means the strategy&apos;s rules are met on closed candles; it is not a prediction or a probability.
        {ev.warn && " This strategy is not statistically validated: treat it as research."}
      </p>
    </section>
  );
}

interface TierStats { resolved: number; signals: number | null; win_rate: number | null; win_rate_ci95: [number, number] | null; expectancy: number | null; expectancy_ci: [number, number] | null; profit_factor: number | null; avg_r: number | null; max_drawdown: number | null; max_consecutive_losses: number | null }
interface FwdStats { recorded: number; qualified: number; comparison: { tiers: Record<string, TierStats>; verdict: string; method: string } }

function ForwardStats() {
  const { data } = usePolling<FwdStats>("/api/signals/stats", 300_000);
  if (!data) return null;
  return (
    <div className="mt-4" data-testid="forward-stats">
      <p className="mb-1 text-xs font-semibold text-muted">Forward test: {data.recorded} recorded setups ({data.qualified} confirmed), hypothetical outcomes</p>
      <table className="w-full text-[11px]">
        <thead><tr className="text-left text-faint"><th>Threshold</th><th>Resolved</th><th>Win % (95% CI)</th><th>$/trade (95% CI)</th><th>PF</th><th>Avg R</th></tr></thead>
        <tbody>
          {Object.entries(data.comparison.tiers).map(([k, t]) => (
            <tr key={k} className="border-t border-line/40 font-mono">
              <td className="font-sans">{k}</td><td>{t.resolved}</td>
              <td>{t.win_rate == null ? "—" : `${Math.round(t.win_rate * 100)}%`}{t.win_rate_ci95 ? ` (${Math.round(t.win_rate_ci95[0] * 100)}–${Math.round(t.win_rate_ci95[1] * 100)})` : ""}</td>
              <td>{t.expectancy ?? "—"}{t.expectancy_ci ? ` (${t.expectancy_ci[0]} – ${t.expectancy_ci[1]})` : ""}</td>
              <td>{t.profit_factor ?? "—"}</td><td>{t.avg_r ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="mt-1 text-[11px] text-warn">{data.comparison.verdict}</p>
    </div>
  );
}

/** Tab 1: three instrument cards; clicking one opens the focused analysis. */
export function MarketAnalyzer() {
  const { data, error } = usePolling<Overview>("/api/analyzer", 20_000);
  const [sel, setSel] = useState<string | null>(null);
  const selected = sel ?? data?.cards[0]?.symbol ?? null;
  return (
    <div className="space-y-4">
      <PageHeader path="/" />
      {error && !data && <Panel><p className="text-sm text-down">{error}</p></Panel>}
      <div className="grid gap-3 md:grid-cols-3" data-testid="instrument-cards">
        {data?.cards.map((c) => <InstrumentCard key={c.symbol} c={c} active={c.symbol === selected} onOpen={() => setSel(c.symbol)} />)}
        {!data && !error && [0, 1, 2].map((i) => <div key={i} className="h-64 animate-pulse rounded-lg border border-line bg-panel" />)}
      </div>
      {data && <p className="text-[11px] text-faint">{data.note}</p>}
      {selected && <Detail symbol={selected} />}
    </div>
  );
}
