"use client";

import type { Timeframe } from "@apex/shared-types";
import { useMemo, useState } from "react";
import { PageHeader } from "@/components/shell/PageHeader";
import { cx, Panel } from "@/components/ui/primitives";
import { CandleChart } from "@/features/charts/CandleChart";
import { useCandles } from "@/features/charts/useCandles";
import { usePolling } from "@/hooks/usePolling";
import {
  ageText, analyzerShapes, BIAS_TONE, DEFAULT_OVERLAYS, fmtPrice, NEWS_TONE,
  type Analysis, type Card, type Level, type OverlayOptions, type Overview,
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
        <span className={cx("rounded border px-2 py-1 text-xs font-semibold tracking-wider", BIAS_TONE[c.bias.direction])} data-testid="bias">{c.bias.label}</span>
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
        <div className="flex justify-between gap-2"><dt className="text-faint">Session</dt><dd>{c.session.name}</dd></div>
        <div className="flex justify-between gap-2"><dt className="text-faint">Structure</dt><dd className="truncate">{c.headline_structure ?? "—"}</dd></div>
        <div className="flex justify-between gap-2"><dt className="text-faint">Liquidity above</dt><dd><LevelLine lv={c.liquidity.above} tick={0.25} /></dd></div>
        <div className="flex justify-between gap-2"><dt className="text-faint">Liquidity below</dt><dd><LevelLine lv={c.liquidity.below} tick={0.25} /></dd></div>
        <div className="flex justify-between gap-2"><dt className="text-faint">Setup</dt><dd>{c.setup ? `${c.setup.strategy} (${c.setup.direction})` : "None"}</dd></div>
        <div className="flex justify-between gap-2"><dt className="text-faint">Setup quality</dt><dd className="font-mono">{c.quality == null ? "—" : `${c.quality}/100`}</dd></div>
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
        <Section letter={a.setup ? "D" : "E"} title={a.setup ? "Potential setup" : "Why wait?"}>
          {a.setup ? (
            <div className="space-y-2" data-testid="setup">
              <p><span className="font-semibold">{a.setup.strategy}</span> · <span className={a.setup.direction === "long" ? "text-up" : "text-down"}>{a.setup.direction.toUpperCase()}</span>{!a.setup.aligned_with_bias && <span className="ml-2 text-warn">opposes the bias</span>}</p>
              <dl className="grid grid-cols-2 gap-x-4 gap-y-1 font-mono text-xs">
                <dt className="text-faint">Entry area</dt><dd>{fmtPrice(a.setup.entry_area[0], tick)} – {fmtPrice(a.setup.entry_area[1], tick)}</dd>
                <dt className="text-faint">Invalidation / stop</dt><dd>{fmtPrice(a.setup.stop, tick)}</dd>
                <dt className="text-faint">Targets</dt><dd>{a.setup.targets.map((t) => fmtPrice(t, tick)).join(" / ")}</dd>
                <dt className="text-faint">R:R T1 gross / net</dt><dd>{a.setup.rr_t1 ? `${a.setup.rr_t1.gross ?? "—"} / ${a.setup.rr_t1.net ?? "—"}` : "—"}</dd>
                <dt className="text-faint">R:R T2 gross / net</dt><dd>{a.setup.rr_t2 ? `${a.setup.rr_t2.gross ?? "—"} / ${a.setup.rr_t2.net ?? "—"}` : "—"}</dd>
                <dt className="text-faint">Risk, 1 contract</dt><dd>{a.setup.risk_1c_usd == null ? "—" : `$${a.setup.risk_1c_usd}`}</dd>
              </dl>
              {a.setup.satisfied.length > 0 && <p className="text-xs"><span className="text-up">Met:</span> {a.setup.satisfied.join(", ")}</p>}
              {a.setup.missing.length > 0 && <p className="text-xs"><span className="text-warn">Missing:</span> {a.setup.missing.join(", ")}</p>}
              <p className="text-xs text-muted">Evidence: {a.setup.evidence.evidence ?? a.setup.evidence.reason ?? "none"}</p>
              <p className="text-[11px] text-faint">{a.setup.note}</p>
              {a.why_wait.length > 0 && <ul className="list-disc pl-5 text-xs text-warn">{a.why_wait.map((w) => <li key={w}>{w}</li>)}</ul>}
            </div>
          ) : (
            <ul className="list-disc space-y-1 pl-5" data-testid="why-wait">{a.why_wait.map((w) => <li key={w}>{w}</li>)}</ul>
          )}
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
            <p className="mb-2 text-xs font-semibold text-muted">Confluence ({a.confluence?.label})</p>
            <p className="mb-2 font-mono text-lg">{a.confluence?.score ?? "—"}/100</p>
            <ul className="space-y-1 text-xs">
              {a.confluence?.factors.map((f) => (
                <li key={f.n} className="flex gap-2"><span className={cx("w-16 shrink-0", f.support === "supports" ? "text-up" : f.support === "opposes" ? "text-down" : "text-faint")}>{f.support}</span><span><span className="text-muted">{f.n}. {f.name}:</span> {f.detail}</span></li>
              ))}
            </ul>
            <p className="mt-2 text-[11px] text-faint">{a.confluence?.method}</p>
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
