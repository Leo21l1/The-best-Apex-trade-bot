"use client";

import type { Timeframe } from "@apex/shared-types";
import { useMemo, useState } from "react";
import { cx, Panel } from "@/components/ui/primitives";
import { CandleChart, type PriceLineSpec } from "@/features/charts/CandleChart";
import { useCandles } from "@/features/charts/useCandles";
import { usePolling } from "@/hooks/usePolling";
import {
  ageText, analyzerShapes, BIAS_TONE, DEFAULT_OVERLAYS, evidenceText, fmtPrice, headline, NEWS_TONE, STRATEGY_NAME,
  type Analysis, type Card, type Factor, type OverlayOptions, type Overview,
} from "@/lib/analyzer";
import { formatEt } from "@/lib/format";

const STATUS_TONE: Record<string, string> = {
  Confirmed: "text-up", Developing: "text-accent", Conflicting: "text-warn", Invalidated: "text-down", "Not applicable": "text-faint",
};
const FACTOR_TONE: Record<string, string> = {
  PASS: "text-up border-up/40 bg-up/10", FAIL: "text-down border-down/40 bg-down/10", UNKNOWN: "text-warn border-warn/40 bg-warn/10",
};

/* ------------------------------------------------------------------------------------------ cards */

function DataPill({ d }: { d: Card["data"] }) {
  const live = d.label === "LIVE";
  const tone = live ? "text-up border-up/40" : d.label === "SIMULATED" ? "text-accent border-accent/40" : d.label === "MARKET CLOSED" || d.label === "HISTORICAL" || d.label === "DELAYED" ? "text-muted border-line" : "text-warn border-warn/50";
  return (
    <span className={cx("inline-flex items-center gap-1 rounded-full border px-2 py-0.5 font-mono text-[10px] tracking-wider", tone)} data-testid="data-label">
      <span className={cx("h-1.5 w-1.5 rounded-full", live ? "bg-up motion-safe:animate-pulse" : "bg-current opacity-60")} aria-hidden />
      {d.label}
    </span>
  );
}

function Change({ d }: { d: Card["data"] }) {
  if (d.change == null) return null;
  const up = d.change >= 0;
  return (
    <span className={cx("font-mono text-xs", up ? "text-up" : "text-down")} title={`vs ${d.prev_session} close ${d.prev_close}`} data-testid="price-change">
      {up ? "+" : ""}{fmtPrice(d.change)} ({up ? "+" : ""}{d.change_pct?.toFixed(2)}%)
    </span>
  );
}

function InstrumentCard({ c, active, onOpen }: { c: Card; active: boolean; onOpen: () => void }) {
  const h = headline(c.state);
  const glow = c.state.includes("BUY SETUP CONFIRMED") ? "apex-glow-up" : c.state.includes("SELL SETUP CONFIRMED") ? "apex-glow-down" : "";
  return (
    <button type="button" onClick={onOpen} data-testid={`card-${c.symbol}`} aria-pressed={active}
      className={cx("apex-panel apex-card flex w-full flex-col rounded-2xl p-4 text-left sm:p-5", glow, active ? "border-accent/70" : "hover:border-accent/40")}>
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="flex items-baseline gap-2">
            <span className="text-xl font-bold tracking-tight">{c.symbol}</span>
            <span className="truncate font-mono text-[11px] text-faint">{c.data.contract ?? ""}</span>
          </p>
          <p className="truncate text-[11px] text-faint">{c.name}</p>
        </div>
        <span className={cx("shrink-0 rounded-lg border px-2.5 py-1 text-xs font-bold tracking-[0.1em]", h.tone)} data-testid="card-state">{h.text}</span>
      </div>
      <div className="mt-4 flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <span className="font-mono text-[28px] leading-none font-semibold tracking-tight" data-testid="price">{c.data.price == null ? "—" : fmtPrice(c.data.price)}</span>
        <Change d={c.data} />
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-2 text-[11px] text-faint">
        <DataPill d={c.data} />
        {c.data.confidence === "LOW CONFIDENCE" && <span className="rounded-full border border-warn/50 px-2 py-0.5 font-mono text-[10px] text-warn" title={c.data.confidence_note}>LOW CONFIDENCE</span>}
        <span>{c.data.price_time ? `${formatEt(c.data.price_time)} · ${ageText(c.data.age_seconds)}` : c.data.note ?? "No data"}</span>
      </div>
      <div className="mt-4 grid grid-cols-3 gap-2 border-t border-line/60 pt-3 text-xs">
        <div><p className="apex-label">Bias</p><p className={cx("mt-1 inline-block rounded-md border px-1.5 text-[11px] font-semibold", BIAS_TONE[c.bias.direction])} data-testid="bias">{c.bias.label}</p></div>
        <div><p className="apex-label">Confluence</p><p className="mt-1 font-mono text-sm font-semibold" data-testid="card-confluence">{c.confluence?.score == null ? "—" : `${c.confluence.score}/5`}</p></div>
        <div><p className="apex-label">Confirmed</p><p className="mt-1 truncate font-mono text-[11px]">{c.last_confirmation ?? "—"}</p></div>
      </div>
      <p className={cx("mt-3 truncate text-[11px] font-semibold", NEWS_TONE[c.news.status ?? ""] ?? "text-faint")} title={c.news.summary ?? ""}>
        <span aria-hidden>● </span>{c.news.status ?? "News status unknown"}
      </p>
    </button>
  );
}

/* ---------------------------------------------------------------------------- 1. market bias */

function MarketBias({ a }: { a: Analysis }) {
  return (
    <Panel title="1 · Market bias">
      <div className="flex flex-wrap items-center gap-3">
        <span className={cx("rounded-lg border px-3 py-1.5 text-base font-bold tracking-[0.1em]", BIAS_TONE[a.bias.direction])}>{a.bias.label}</span>
        <p className="min-w-0 flex-1 text-base text-text sm:text-lg" data-testid="bias-sentence">{a.bias.sentence}</p>
      </div>
      <div className="mt-3 flex flex-wrap gap-2 text-[11px]">
        {[["1h", a.bias.htf_1h], ["15m", a.bias.swing_15m], ["5m", a.bias.short_term_5m]].map(([k, v]) => (
          <span key={k} className="rounded-full border border-line px-2 py-0.5 text-muted">{k} <span className={v === "bullish" ? "text-up" : v === "bearish" ? "text-down" : "text-faint"}>{v ?? "—"}</span></span>
        ))}
        <span className="rounded-full border border-line px-2 py-0.5 text-muted">{a.session.name}</span>
      </div>
      {a.bias.pullback && <p className="mt-2 text-xs text-muted">The 5-minute structure ({a.bias.short_term_5m}) differs from the broader bias: a pullback, not a contradiction.</p>}
      <p className="mt-2 text-xs text-faint">{a.narrative}</p>
    </Panel>
  );
}

/* -------------------------------------------------------------- 2-3. current setup and entry plan */

function planOf(a: Analysis) {
  const rec = a.signal.recorded ?? null;
  const best = a.signal.best;
  if (rec) {
    return { entry: rec.entry, zone: rec.entry_zone, stop: rec.stop, tp1: rec.tp1, tp2: rec.tp2, rr1: rec.net_rr_tp1, rr2: rec.rr_tp2?.net ?? null,
      stopBasis: rec.stop_basis, tp1Basis: rec.tp1_basis, tp2Basis: rec.tp2_basis, risk: rec.risk_usd_1c ?? null, riskWarning: rec.risk_warning ?? null,
      direction: rec.direction };
  }
  if (best) {
    const l = best.levels;
    return { entry: l.entry, zone: l.entry_zone, stop: l.stop, tp1: l.tp1, tp2: l.tp2, rr1: l.rr_tp1?.net ?? null, rr2: l.rr_tp2?.net ?? null,
      stopBasis: l.stop_basis, tp1Basis: l.tp1_basis, tp2Basis: l.tp2_basis, risk: l.risk_usd_1c ?? null, riskWarning: l.risk_warning ?? null,
      direction: best.direction };
  }
  return null;
}

function Level({ label, value, tone, title, testId }: { label: string; value: React.ReactNode; tone?: string; title?: string; testId?: string }) {
  return (
    <div className="min-w-0 rounded-xl border border-line/70 bg-panel-2/50 px-3 py-2" title={title} data-testid={testId}>
      <p className="apex-label">{label}</p>
      <p className={cx("mt-1 truncate font-mono text-[15px] font-semibold", tone)}>{value}</p>
    </div>
  );
}

function SetupPanel({ a }: { a: Analysis }) {
  const sig = a.signal;
  const rec = sig.recorded ?? null;
  const state = sig.display_state ?? sig.state;
  const h = headline(state);
  const best = sig.best;
  const tick = a.tick_size;
  const lv = planOf(a);
  const factors = rec?.factors?.length ? rec.factors : sig.factors;
  const score = factors.filter((f) => f.status === "PASS").length;
  const ev = evidenceText(rec?.evidence ?? best?.evidence);
  const confirmed = state.includes("CONFIRMED");
  const strategy = rec?.strategy_id ?? best?.strategy_id;
  const missing = [...factors.filter((f) => f.status !== "PASS").map((f) => `${f.name}: ${f.detail}`),
    ...(!rec && best && !best.confirmed ? best.missing.filter((m) => !m.includes("confluences")) : [])];
  const border = confirmed ? (state.includes("BUY") ? "apex-glow-up" : "apex-glow-down") : "";
  return (
    <section className={cx("apex-panel rounded-2xl p-4 sm:p-5", border)} data-testid="signal-panel">
      <p className="apex-label">2 · Current setup</p>
      <div className="mt-1 flex flex-wrap items-center gap-3">
        <h2 className="text-xl font-bold tracking-wide sm:text-2xl">
          <span>{a.symbol}</span> <span className="text-faint">—</span> <span className={cx(h.tone.split(" ")[0])} data-testid="signal-state">{state}</span>
        </h2>
        <span className="rounded-full border border-line px-2.5 py-0.5 font-mono text-sm" data-testid="signal-confluence">Confluence {score}/5</span>
        {strategy && <span className="text-sm text-muted">{STRATEGY_NAME[strategy] ?? strategy}</span>}
      </div>
      <div className="mt-2 flex flex-wrap gap-2">
        {(rec || best) && <span className={cx("rounded-full border px-2.5 py-0.5 text-[11px] font-bold tracking-wide", ev.warn ? "border-warn/60 bg-warn/10 text-warn" : "border-up/50 text-up")} data-testid="evidence">Historical evidence: {ev.text}</span>}
        {a.data.label !== "LIVE" && <span className="rounded-full border border-warn/60 bg-warn/10 px-2.5 py-0.5 text-[11px] font-bold text-warn">DATA {a.data.label}</span>}
        {a.news && ["NEWS WINDOW ACTIVE", "POST-NEWS VOLATILITY", "NEWS DATA STALE / UNAVAILABLE"].includes(a.news.status) && (
          <span className="rounded-full border border-warn/60 bg-warn/10 px-2.5 py-0.5 text-[11px] font-bold text-warn">{a.news.status}</span>
        )}
      </div>
      {rec?.status_reason && <p className="mt-2 text-xs text-warn">{rec.status_reason}</p>}
      {sig.note && !best && <p className="mt-2 text-xs text-muted">{sig.note}</p>}

      {lv ? (
        <>
          <p className="apex-label mt-4">3 · Entry plan {confirmed ? "" : "(not confirmed - shown for reference)"}</p>
          <div className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-4 lg:grid-cols-8" data-testid="signal-levels">
            <div className="col-span-2"><Level label={`Entry (${lv.direction === "long" ? "buy" : "sell"} zone)`} value={<>{fmtPrice(lv.entry, tick)} <span className="text-[11px] font-normal text-faint">{fmtPrice(lv.zone[0], tick)}–{fmtPrice(lv.zone[1], tick)}</span></>} /></div>
            <Level label="Stop loss" value={fmtPrice(lv.stop, tick)} tone="text-down" title={lv.stopBasis ?? ""} />
            <Level label="TP1" value={fmtPrice(lv.tp1, tick)} tone="text-up" title={lv.tp1Basis ?? ""} />
            <Level label="TP2" value={fmtPrice(lv.tp2, tick)} tone="text-up" title={lv.tp2Basis ?? ""} />
            <Level label="Net R:R TP1/TP2" value={`${lv.rr1 ?? "—"} / ${lv.rr2 ?? "—"}`} />
            <Level label="Risk / contract" value={lv.risk == null ? "—" : `$${lv.risk.toLocaleString("en-US", { maximumFractionDigits: 0 })}`} tone={lv.riskWarning ? "text-warn" : undefined} testId="risk-usd" />
            <Level label={rec ? `Confirmed · expires ${rec.expires_at_et}` : "Confirmed"} value={rec ? rec.confirmed_at_et.slice(11) : "—"} />
          </div>
          {lv.riskWarning && <p className="mt-2 rounded-lg border border-warn/50 bg-warn/10 px-3 py-2 text-xs text-warn" data-testid="risk-warning">{lv.riskWarning}</p>}
          <p className="mt-2 text-[11px] text-faint">Stop: {lv.stopBasis ?? "—"} · TP1: {lv.tp1Basis ?? "—"}{lv.tp2Basis ? ` · TP2: ${lv.tp2Basis}` : ""}. Analytical levels, not orders or guaranteed fills. Expires {rec ? `at ${rec.expires_at_et}` : "30 min after confirmation"}; invalidated by an opposite 5m structure shift before entry.</p>
        </>
      ) : (
        <p className="mt-4 text-sm text-muted">No strategy has produced a plan with valid structural levels right now.</p>
      )}

      <div className="mt-4 grid gap-3 md:grid-cols-2">
        <div>
          <p className="mb-1 text-xs font-semibold text-up">Why (confirmations)</p>
          <ul className="space-y-0.5 text-xs" data-testid="confirmations">
            {factors.filter((f) => f.status === "PASS").map((f) => <li key={f.n}>✓ {f.name}: <span className="text-muted">{f.detail}</span></li>)}
            {!factors.some((f) => f.status === "PASS") && <li className="text-faint">None yet.</li>}
          </ul>
        </div>
        <div>
          <p className="mb-1 text-xs font-semibold text-warn">{confirmed ? "Notes" : "Still missing / why wait"}</p>
          <ul className="space-y-0.5 text-xs" data-testid="missing">
            {(confirmed ? [] : [...missing, ...a.why_wait.filter((w) => !missing.includes(w))]).slice(0, 7).map((m) => <li key={m}>• {m}</li>)}
            {(confirmed || !missing.length) && <li className="text-faint">Nothing missing for the technical rules.</li>}
          </ul>
          {!confirmed && a.why_wait.length > 0 && <ul className="sr-only" data-testid="why-wait">{a.why_wait.map((w) => <li key={w}>{w}</li>)}</ul>}
        </div>
      </div>
      <p className="mt-3 text-[11px] text-faint">
        A technical confirmation means the strategy&apos;s rules are met on closed candles; it is not a prediction, a probability or a proven edge.
        {ev.warn && " This strategy is RESEARCH / UNVALIDATED."}
      </p>
    </section>
  );
}

/* ----------------------------------------------------------------------------- 4. confluence */

function ConfluencePanel({ a }: { a: Analysis }) {
  const fs: Factor[] = a.signal.recorded?.factors?.length ? a.signal.recorded.factors : a.signal.factors;
  return (
    <Panel title={`4 · Confluence (${fs.filter((f) => f.status === "PASS").length}/5, threshold ${a.confluence?.threshold ?? 4}/5)`}>
      <ul className="divide-y divide-line/50" data-testid="five-factors">
        {fs.map((f) => (
          <li key={f.n} className="flex items-start gap-3 py-2 first:pt-0 last:pb-0">
            <span className={cx("w-[72px] shrink-0 rounded-md border px-1.5 py-0.5 text-center font-mono text-[11px] font-bold", FACTOR_TONE[f.status])}>{f.status}</span>
            <div className="min-w-0 text-sm">
              <p className="font-medium">{f.n}. {f.name}</p>
              <p className="text-xs text-muted">{f.detail}</p>
            </div>
          </li>
        ))}
      </ul>
      <p className="mt-3 text-[11px] text-faint">BUY {a.signal.direction_scores?.bullish ?? "—"}/5 · SELL {a.signal.direction_scores?.bearish ?? "—"}/5. UNKNOWN never counts as PASS. A count is a checklist, not a probability.</p>
    </Panel>
  );
}

/* ---------------------------------------------------------------------- 5. market structure */

function StructurePanel({ a }: { a: Analysis }) {
  const tick = a.tick_size;
  const keyLevels = a.levels.filter((l) => l.valid).sort((x, y) => Math.abs(x.distance) - Math.abs(y.distance)).slice(0, 12)
    .sort((x, y) => y.price - x.price);
  return (
    <Panel title="5 · Market structure & key levels">
      {a.observations.length ? (
        <ul className="mb-3 space-y-1 text-sm" data-testid="observations">{a.observations.map((o) => <li key={o}>▸ {o}</li>)}</ul>
      ) : <p className="mb-3 text-sm text-faint">No notable structure events, sweeps or zone retests right now.</p>}
      {keyLevels.length ? (
        <div className="-mx-4 overflow-x-auto">
          <table className="w-full min-w-[560px] text-xs" data-testid="levels">
            <thead><tr className="text-left">{["Level", "Price", "TF", "Status", "Distance"].map((h) => <th key={h} className="apex-label px-2 py-1.5 font-medium first:pl-4">{h}</th>)}</tr></thead>
            <tbody>
              {keyLevels.map((l) => {
                const isPrice = Math.abs(l.distance) < tick * 0.5;
                return (
                  <tr key={`${l.label}-${l.price}`} className={cx("border-t border-line/40 hover:bg-panel-2/40", isPrice && "bg-accent/5")}>
                    <td className="px-2 py-1.5 pl-4">{l.label}</td>
                    <td className="px-2 py-1.5 font-mono">{l.top != null && l.bottom != null ? `${fmtPrice(l.bottom, tick)}–${fmtPrice(l.top, tick)}` : fmtPrice(l.price, tick)}</td>
                    <td className="px-2 py-1.5 font-mono text-muted">{l.timeframe}</td>
                    <td className="px-2 py-1.5 text-muted">{l.state}</td>
                    <td className={cx("px-2 py-1.5 font-mono", l.distance > 0 ? "text-down/80" : "text-up/80")}>{l.distance > 0 ? "+" : ""}{fmtPrice(l.distance, tick)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : <p className="text-sm text-faint">No valid levels detected from the available data.</p>}
      <p className="mt-2 text-[11px] text-faint">Liquidity, FVGs and order blocks are inferred from candles; resting orders are not observable.</p>
    </Panel>
  );
}

/* --------------------------------------------------------------------------------- 6. chart */

function AnalyzerChart({ a }: { a: Analysis }) {
  const [tf, setTf] = useState<Timeframe>("5m");
  const [o, setO] = useState<OverlayOptions>(DEFAULT_OVERLAYS);
  const [plan, setPlan] = useState(true);
  const [vol, setVol] = useState(true);
  const { bars } = useCandles(a.symbol, tf);
  const times = useMemo(() => bars.map((b) => b.t), [bars]);
  const smc = useMemo(() => analyzerShapes(a, tf, times, o), [a, tf, times, o]);
  const lv = planOf(a);
  const lines: PriceLineSpec[] = useMemo(() => {
    if (!plan || !lv) return [];
    const out: PriceLineSpec[] = [{ price: lv.entry, title: "ENTRY", color: "#22d3ee" }];
    if (lv.stop != null) out.push({ price: lv.stop, title: "SL", color: "#ef4444", dashed: true });
    if (lv.tp1 != null) out.push({ price: lv.tp1, title: "TP1", color: "#10b981", dashed: true });
    if (lv.tp2 != null) out.push({ price: lv.tp2, title: "TP2", color: "#10b981", dashed: true });
    return out;
  }, [plan, lv]);
  const toggle = (k: keyof OverlayOptions) => setO((p) => ({ ...p, [k]: !p[k] }));
  return (
    <Panel title="6 · Chart" actions={
      <div className="flex items-center gap-0.5 rounded-full border border-line/70 bg-panel/60 p-0.5 text-[11px]" role="group" aria-label="Timeframe">
        {(["1m", "5m", "15m", "1h"] as Timeframe[]).map((t) => (
          <button key={t} type="button" onClick={() => setTf(t)} aria-pressed={tf === t}
            className={cx("min-w-9 rounded-full px-2.5 py-1 font-mono", tf === t ? "bg-accent/20 text-text" : "text-muted hover:text-text")}>{t}</button>
        ))}
      </div>
    }>
      <div className="mb-3 flex flex-wrap gap-x-4 gap-y-2 text-[11px] text-muted" data-testid="overlay-toggles">
        {([["fvg", "FVGs"], ["ob", "Order blocks"], ["liquidity", "Liquidity"], ["structure", "BOS / CHoCH"], ["session", "Session levels"]] as [keyof OverlayOptions, string][]).map(([k, label]) => (
          <label key={k} className="flex cursor-pointer items-center gap-1.5"><input type="checkbox" className="accent-cyan-400" checked={o[k]} onChange={() => toggle(k)} />{label}</label>
        ))}
        <label className="flex cursor-pointer items-center gap-1.5"><input type="checkbox" className="accent-cyan-400" checked={plan} onChange={() => setPlan((p) => !p)} data-testid="plan-toggle" />Entry / SL / TP</label>
        <label className="flex cursor-pointer items-center gap-1.5"><input type="checkbox" className="accent-cyan-400" checked={vol} onChange={() => setVol((p) => !p)} />Volume</label>
      </div>
      {bars.length ? (
        <CandleChart bars={bars} tick={a.tick_size} daily={false} seriesKey={`${a.symbol}:${tf}`} height="h-[360px] w-full sm:h-[460px]"
          toggles={{ ema: false, vwap: true, levels: false, signals: false, news: false }} smc={smc} lines={lines} showVolume={vol} />
      ) : <p className="py-16 text-center text-sm text-faint">No chart data for {a.symbol}.</p>}
      <p className="mt-2 text-[11px] text-faint">Only the nearest active zones are drawn. Plan lines are hypothetical analysis levels.</p>
    </Panel>
  );
}

/* ----------------------------------------------------------------------- 7. advanced analysis */

interface TierStats { resolved: number; signals: number | null; win_rate: number | null; win_rate_ci95: [number, number] | null; expectancy: number | null; expectancy_ci: [number, number] | null; profit_factor: number | null; avg_r: number | null; max_drawdown: number | null; max_consecutive_losses: number | null }
interface FwdStats { recorded: number; qualified: number; opportunities?: number; comparison: { tiers: Record<string, TierStats>; verdict: string; method: string } }

function ForwardStats() {
  const { data } = usePolling<FwdStats>("/api/signals/stats", 300_000);
  if (!data) return null;
  return (
    <div className="mt-4" data-testid="forward-stats">
      <p className="mb-1 text-xs font-semibold text-muted">Forward test: {data.recorded} recorded detections, {data.opportunities ?? "—"} unique opportunities ({data.qualified} confirmed); hypothetical outcomes</p>
      <div className="-mx-1 overflow-x-auto">
        <table className="w-full min-w-[460px] text-[11px]">
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
      </div>
      <p className="mt-1 text-[11px] text-warn">{data.comparison.verdict}</p>
    </div>
  );
}

function Advanced({ a }: { a: Analysis }) {
  const tick = a.tick_size;
  return (
    <details className="apex-panel group rounded-xl p-4" data-testid="advanced">
      <summary className="cursor-pointer text-[12px] font-semibold tracking-[0.12em] text-muted uppercase">7 · Advanced analysis <span className="text-faint normal-case tracking-normal">(strategies, timeframes, forward test)</span></summary>
      <div className="mt-4 grid gap-6 lg:grid-cols-2">
        <div>
          <p className="mb-1 text-xs font-semibold text-muted">Strategy signals evaluated now</p>
          <ul className="space-y-1 text-xs" data-testid="candidates">
            {a.signal.candidates.length ? a.signal.candidates.map((c) => (
              <li key={`${c.strategy_id}-${c.trigger_at}`}>{STRATEGY_NAME[c.strategy_id] ?? c.strategy_id} {c.direction} — {c.score}/5{c.confirmed ? " · confirmed" : ` · missing: ${c.missing.slice(0, 2).join("; ")}`}</li>
            )) : <li className="text-faint">No strategy has triggered in the last 30 minutes.</li>}
          </ul>
          <p className="mt-2 text-[11px] text-faint">{a.confluence?.label}</p>
          <ForwardStats />
        </div>
        <div>
          <p className="mb-2 text-xs font-semibold text-muted">Strategies by family</p>
          {Object.entries(a.strategies).map(([fam, rows]) => (
            <div key={fam} className="mb-3">
              <p className="apex-label">{fam}</p>
              <ul className="text-xs">
                {rows.map((r) => <li key={r.id} className="flex justify-between gap-2 py-0.5"><span>{r.name}</span><span className={STATUS_TONE[r.status]} title={r.detail}>{r.status}</span></li>)}
              </ul>
            </div>
          ))}
        </div>
        <div className="lg:col-span-2">
          <p className="mb-2 text-xs font-semibold text-muted">Timeframes</p>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[520px] text-xs">
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
      </div>
    </details>
  );
}

/* ----------------------------------------------------------------------------------- page */

function Detail({ symbol }: { symbol: string }) {
  const { data: a, error } = usePolling<Analysis>(`/api/analyzer/${symbol}`, 20_000);
  if (error && !a) return <Panel><p className="text-sm text-down">{error}</p></Panel>;
  if (!a) return <div className="apex-panel h-64 animate-pulse rounded-2xl" aria-busy="true" aria-label={`Loading ${symbol} analysis`} />;
  return (
    <div className="space-y-4" data-testid="analysis-detail" key={symbol}>
      <MarketBias a={a} />
      <SetupPanel a={a} />
      <div className="grid gap-4 xl:grid-cols-2">
        <ConfluencePanel a={a} />
        <StructurePanel a={a} />
      </div>
      <AnalyzerChart a={a} />
      <Advanced a={a} />
      <p className="text-[11px] text-faint">{a.disclaimer}</p>
    </div>
  );
}

/** Tab 1: three instrument cards; clicking one opens its analysis. */
export function MarketAnalyzer() {
  const { data, error } = usePolling<Overview>("/api/analyzer", 20_000);
  const [sel, setSel] = useState<string | null>(null);
  const selected = sel ?? data?.cards[0]?.symbol ?? null;
  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Market Analyzer</h1>
          <p className="text-sm text-muted">Live ES · NQ · MNQ analysis from closed candles: structure, liquidity, zones, setups and news.</p>
        </div>
        {data && <p className="font-mono text-[11px] text-faint">Updated {formatEt(data.as_of)}</p>}
      </div>
      {error && !data && <Panel><p className="text-sm text-down">{error}</p></Panel>}
      <div className="grid gap-3 md:grid-cols-3" data-testid="instrument-cards">
        {data?.cards.map((c) => <InstrumentCard key={c.symbol} c={c} active={c.symbol === selected} onOpen={() => setSel(c.symbol)} />)}
        {!data && !error && [0, 1, 2].map((i) => <div key={i} className="apex-panel h-56 animate-pulse rounded-2xl" />)}
      </div>
      {data && <p className="text-[11px] text-faint">{data.note}</p>}
      {selected && <Detail symbol={selected} />}
    </div>
  );
}
