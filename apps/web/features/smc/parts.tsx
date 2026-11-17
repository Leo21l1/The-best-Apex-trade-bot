"use client";

import type { Confluence, LiquidityLevel, LiquidityMap, SmcAnalysis, SmcPattern } from "@apex/shared-types";
import Link from "next/link";
import { Fragment, useEffect, useState } from "react";
import { cx } from "@/components/ui/primitives";
import { usePolling } from "@/hooks/usePolling";
import { formatEt } from "@/lib/format";
import { DEFAULT_SMC, familyOf, PATTERN_NAMES, SMC_LABELS, type SmcToggles } from "@/lib/smc";

export function useSmc(symbol: string, tf: string, maxZones = 12) {
  return usePolling<SmcAnalysis>(`/api/smc/analysis?symbol=${symbol}&timeframe=${tf}&limit=400&max_zones=${maxZones}`, 30_000);
}

export function useConfluence(symbol: string) {
  return usePolling<Confluence>(`/api/smc/confluence?symbol=${symbol}`, 30_000);
}

const STORE = "apex.smc.toggles";

/** Overlay toggles + quality/zone filters, remembered per browser. */
export function useSmcSettings() {
  const [v, setV] = useState<{ toggles: SmcToggles; minQuality: number; maxZones: number }>({ toggles: DEFAULT_SMC, minQuality: 50, maxZones: 4 });
  useEffect(() => {
    try {
      const raw = localStorage.getItem(STORE);
      // eslint-disable-next-line react-hooks/set-state-in-effect -- restoring a saved preference after mount (SSR-safe)
      if (raw) setV((p) => ({ ...p, ...JSON.parse(raw), toggles: { ...DEFAULT_SMC, ...JSON.parse(raw).toggles } }));
    } catch { /* storage unavailable: defaults */ }
  }, []);
  const update = (n: typeof v) => {
    setV(n);
    try { localStorage.setItem(STORE, JSON.stringify(n)); } catch { /* ignore */ }
  };
  return [v, update] as const;
}

const COLORS: Record<string, string> = {
  sweeps: "text-yellow-300", orderBlocks: "text-up", fvg: "text-emerald-300", structure: "text-sky-400",
  supplyDemand: "text-amber-400", equalLevels: "text-slate-300",
};

export function SmcToggleBar({ value, onChange }: {
  value: { toggles: SmcToggles; minQuality: number; maxZones: number };
  onChange: (v: { toggles: SmcToggles; minQuality: number; maxZones: number }) => void;
}) {
  const t = value.toggles;
  return (
    <div className="flex flex-wrap items-center gap-1" role="group" aria-label="Smart money overlays">
      {(Object.keys(SMC_LABELS) as (keyof typeof SMC_LABELS)[]).map((k) => (
        <button key={k} type="button" aria-pressed={t[k]} onClick={() => onChange({ ...value, toggles: { ...t, [k]: !t[k] } })}
          className={cx("rounded border px-2 py-1 text-[11px] transition-colors", t[k] ? `border-line bg-panel-2 ${COLORS[k]}` : "border-line/60 text-faint hover:text-muted")}>
          {SMC_LABELS[k]}
        </button>
      ))}
      <label className="ml-1 flex items-center gap-1 text-[11px] text-faint">
        <input type="checkbox" checked={t.showInvalidated} onChange={(e) => onChange({ ...value, toggles: { ...t, showInvalidated: e.target.checked } })} />
        invalidated
      </label>
      <label className="flex items-center gap-1 text-[11px] text-faint">
        min quality
        <input aria-label="Minimum pattern quality" type="range" min={0} max={90} step={10} value={value.minQuality}
          onChange={(e) => onChange({ ...value, minQuality: Number(e.target.value) })} className="w-20" />
        <span className="w-5 font-mono">{value.minQuality}</span>
      </label>
      <label className="flex items-center gap-1 text-[11px] text-faint">
        max/type
        <select aria-label="Maximum visible zones per type" value={value.maxZones} onChange={(e) => onChange({ ...value, maxZones: Number(e.target.value) })}
          className="rounded border border-line bg-panel-2 px-1 py-0.5">
          {[2, 4, 6, 8, 12].map((n) => <option key={n} value={n}>{n}</option>)}
        </select>
      </label>
    </div>
  );
}

const STATUS_STYLE: Record<string, string> = {
  confirmed_rejection: "text-up", confirmed: "text-sky-400", active: "text-up", open: "text-up", mitigated: "text-warn",
  partially_filled: "text-warn", candidate: "text-muted", breakout_continuation: "text-accent", invalidated: "text-faint line-through",
  filled: "text-faint", swept: "text-faint",
};

/** Smart Money Analysis panel: every detected pattern, filterable, click to inspect. */
export function PatternList({ analysis, selected, onSelect }: { analysis: SmcAnalysis | null; selected: string | null; onSelect: (p: SmcPattern) => void }) {
  const [filter, setFilter] = useState("all");
  if (!analysis) return <p className="text-xs text-faint">Loading analysis…</p>;
  if (!analysis.patterns.length) return <p className="text-xs text-faint">No patterns in the analysed bars ({analysis.bars}).</p>;
  const rows = analysis.patterns.filter((p) => filter === "all" || familyOf(p.type) === filter || (filter === "other" && !familyOf(p.type)));
  return (
    <div data-testid="smc-patterns">
      <div className="mb-2 flex flex-wrap items-center gap-2 text-xs text-muted">
        <span>Trend <b className={analysis.trend === "bullish" ? "text-up" : analysis.trend === "bearish" ? "text-down" : ""}>{analysis.trend}</b></span>
        {analysis.premium_discount && <span>· {analysis.premium_discount.zone} ({Math.round(analysis.premium_discount.position * 100)}%)</span>}
        {analysis.indicators.volatility_state && <span>· volatility {String(analysis.indicators.volatility_state)}</span>}
        {analysis.indicators.relative_volume != null && <span>· RVOL {String(analysis.indicators.relative_volume)}</span>}
        <select aria-label="Pattern filter" value={filter} onChange={(e) => setFilter(e.target.value)} className="ml-auto rounded border border-line bg-panel-2 px-1 py-0.5">
          <option value="all">All</option>
          {Object.entries(SMC_LABELS).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
          <option value="other">Other</option>
        </select>
      </div>
      <ul className="max-h-80 space-y-1 overflow-y-auto pr-1 text-xs">
        {rows.map((p) => (
          <li key={p.id}>
            <button type="button" onClick={() => onSelect(p)} data-testid={`pattern-${p.type}`}
              className={cx("flex w-full items-center gap-2 rounded border px-2 py-1.5 text-left transition-colors hover:border-accent/50",
                selected === p.id ? "border-accent bg-accent/10" : "border-line/60")}>
              <span className={cx("h-2 w-2 shrink-0 rounded-full", p.direction === "bullish" ? "bg-up" : p.direction === "bearish" ? "bg-down" : "bg-faint")} />
              <span className="min-w-0 flex-1 truncate">{PATTERN_NAMES[p.type] ?? p.type}</span>
              <span className={cx("font-mono", STATUS_STYLE[p.status])}>{p.status.replace(/_/g, " ")}</span>
              <span className="w-14 text-right font-mono text-faint">{formatEt(p.confirmed_at ?? p.formed_at)}</span>
            </button>
          </li>
        ))}
      </ul>
      <p className="mt-2 text-[11px] text-faint">{analysis.disclaimer}</p>
    </div>
  );
}

/** Why a pattern was detected: rule, timestamps, bounds, status, invalidation, evidence. */
export function PatternInspector({ p, analysis, onClose }: { p: SmcPattern; analysis: SmcAnalysis | null; onClose: () => void }) {
  const rows: [string, React.ReactNode][] = [
    ["Instrument", `${analysis?.symbol ?? ""} · ${analysis?.timeframe ?? ""}`],
    ["Pattern", PATTERN_NAMES[p.type] ?? p.type],
    ["Direction", p.direction],
    ["Formed", formatEt(p.formed_at, true)],
    ["Confirmed", p.confirmed_at ? formatEt(p.confirmed_at, true) : "not yet (candidate)"],
    ["Boundaries", p.top === p.bottom ? String(p.top) : `${p.bottom} – ${p.top}`],
    ["Status", p.status.replace(/_/g, " ")],
    ["Invalidation", p.invalidation],
    ["Heuristic quality", `${p.quality}/100 (rule-based score, not a probability)`],
    ["Data source", analysis ? `${analysis.data.provider} · ${analysis.data.quality.toUpperCase()}` : "—"],
  ];
  return (
    <div role="dialog" aria-label="Pattern inspector" data-testid="pattern-inspector" className="animate-[slidein_.18s_ease-out] rounded-lg border border-accent/40 bg-panel p-4 text-xs">
      <div className="mb-2 flex items-center justify-between">
        <h3 className="text-sm font-semibold text-text">{PATTERN_NAMES[p.type] ?? p.type}</h3>
        <button type="button" onClick={onClose} aria-label="Close inspector" className="text-faint hover:text-text">✕</button>
      </div>
      <dl className="grid grid-cols-[7.5rem_1fr] gap-x-3 gap-y-1">
        {rows.map(([k, v]) => (<Fragment key={k}><dt className="text-faint">{k}</dt><dd className="text-text">{v}</dd></Fragment>))}
      </dl>
      <h4 className="mt-3 mb-1 font-medium text-muted">Why it was detected</h4>
      <ul className="space-y-1 text-muted">{p.evidence.map((e) => <li key={e}>• {e}</li>)}</ul>
    </div>
  );
}

function LevelRow({ l, onSelect, active }: { l: LiquidityLevel; onSelect: (l: LiquidityLevel) => void; active: boolean }) {
  return (
    <li>
      <button type="button" onClick={() => onSelect(l)} data-testid="liquidity-level"
        className={cx("flex w-full items-center gap-2 rounded px-2 py-1 text-left font-mono hover:bg-panel-2", active && "bg-accent/15 text-accent")}>
        <span className="w-20">{l.price}</span>
        <span className="min-w-0 flex-1 truncate font-sans text-muted">{l.timeframe ? `${l.timeframe} ` : ""}{l.name}</span>
        <span className="text-faint">{l.distance_atr} ATR</span>
        <span className="w-10 text-right text-faint">{l.score}</span>
      </button>
    </li>
  );
}

/** Nearest inferred liquidity levels above and below price; clicking highlights one on the chart. */
export function LiquidityMapPanel({ map, selected, onSelect }: { map: LiquidityMap | null | undefined; selected: number | null; onSelect: (l: LiquidityLevel) => void }) {
  if (!map) return <p className="text-xs text-faint">Loading levels…</p>;
  if (map.price === null) return <p className="text-xs text-faint">No current price.</p>;
  return (
    <div data-testid="liquidity-map" className="text-xs">
      <p className="mb-1 text-[11px] uppercase tracking-wider text-down">Above</p>
      <ul className="space-y-0.5">{[...map.nearest_above].reverse().map((l) => <LevelRow key={`a${l.price}${l.name}`} l={l} onSelect={onSelect} active={selected === l.price} />)}</ul>
      <div className="my-2 flex items-center gap-2 font-mono text-text"><span className="h-px flex-1 bg-accent/50" />{map.price}<span className="h-px flex-1 bg-accent/50" /></div>
      <p className="mb-1 text-[11px] uppercase tracking-wider text-up">Below</p>
      <ul className="space-y-0.5">{map.nearest_below.map((l) => <LevelRow key={`b${l.price}${l.name}`} l={l} onSelect={onSelect} active={selected === l.price} />)}</ul>
      <p className="mt-2 text-[11px] text-faint">{map.label} {map.ranking}</p>
    </div>
  );
}

const OUTCOME_STYLE: Record<Confluence["outcome"], string> = {
  "BUY BIAS": "border-up/50 bg-up/10 text-up", "SHORT BIAS": "border-down/50 bg-down/10 text-down",
  WAIT: "border-line bg-panel-2 text-muted", "NO TRADE": "border-warn/50 bg-warn/10 text-warn",
};

/** Multi-timeframe confluence: families, gates, plan with risk and invalidation. */
export function ConfluencePanel({ c }: { c: Confluence | null }) {
  if (!c) return <p className="text-xs text-faint">Loading confluence…</p>;
  const p = c.plan;
  return (
    <div data-testid="confluence" className="space-y-3 text-xs">
      <div className="flex flex-wrap items-center gap-2">
        <span className={cx("rounded border px-2 py-1 font-mono text-sm font-semibold tracking-wider", OUTCOME_STYLE[c.outcome])} data-testid="confluence-outcome">{c.headline}</span>
        <span className="rounded border border-warn/40 px-1.5 py-0.5 font-mono text-[10px] text-warn">{c.validation}</span>
        <span className="text-faint">score {c.score}/{c.max_score} (heuristic)</span>
      </div>
      <div className="grid gap-2 sm:grid-cols-3">
        {c.families.map((f) => (
          <div key={f.family} className={cx("rounded border p-2", f.vote > 0 ? "border-up/40" : f.vote < 0 ? "border-down/40" : "border-line")}>
            <p className="mb-1 flex justify-between font-medium capitalize text-text">{f.family}
              <span className={f.vote > 0 ? "text-up" : f.vote < 0 ? "text-down" : "text-faint"}>{f.vote > 0 ? "bullish" : f.vote < 0 ? "bearish" : "none"}</span></p>
            {f.evidence.map((e) => <p key={e} className="text-muted">{e}</p>)}
          </div>
        ))}
      </div>
      <div className="flex flex-wrap gap-2">
        {Object.entries(c.timeframes).map(([tf, v]) => (
          <span key={tf} className="rounded border border-line px-1.5 py-0.5 font-mono"><span className="text-faint">{tf}</span> <span className={v.trend === "bullish" ? "text-up" : v.trend === "bearish" ? "text-down" : "text-muted"}>{v.trend}</span></span>
        ))}
      </div>
      {c.gates.length > 0 && <ul className="rounded border border-warn/40 bg-warn/5 p-2 text-warn">{c.gates.map((g) => <li key={g}>⚠ {g}</li>)}</ul>}
      {p && (
        <div className="rounded border border-line p-2" data-testid="confluence-plan">
          <dl className="grid grid-cols-[7rem_1fr] gap-x-3 gap-y-1">
            <dt className="text-faint">Direction</dt><dd className={p.direction === "long" ? "text-up" : "text-down"}>{p.direction}</dd>
            <dt className="text-faint">Entry zone</dt><dd className="font-mono">{p.entry_zone[0]} – {p.entry_zone[1]}</dd>
            <dt className="text-faint">Stop reference</dt><dd className="font-mono">{p.stop} <span className="font-sans text-faint">({p.stop_basis})</span></dd>
            <dt className="text-faint">Targets</dt><dd className="font-mono">{p.targets.map((t) => `${t.price} (${t.basis})`).join(" · ") || "—"}</dd>
            <dt className="text-faint">Reward:risk</dt><dd className={cx("font-mono", p.rr_ok ? "text-text" : "text-warn")}>{p.reward_risk ?? "—"}</dd>
            <dt className="text-faint">Invalidation</dt><dd>{p.invalidation}</dd>
            <dt className="text-faint">Entry conditions</dt><dd>{p.entry_conditions.join(" ")}</dd>
          </dl>
          <Link href={`/terminal?symbol=${c.symbol}&side=${p.direction === "long" ? "buy" : "sell"}&stop=${p.stop}&target=${p.targets[0]?.price ?? ""}`}
            className="mt-2 inline-block rounded border border-accent/50 px-2 py-1 text-accent hover:bg-accent/10" data-testid="plan-to-terminal">
            Open in terminal (paper / manual plan) →
          </Link>
        </div>
      )}
      <p className="text-faint">News: {c.news.summary} · Data: {c.data.provider} {c.data.quality.toUpperCase()}{c.data.stale ? " (stale)" : ""} · Risk: {c.risk?.known ? `budget $${c.risk.risk_budget}` : "account unknown"}{c.risk && !c.risk.rules_verified ? " (rules unverified)" : ""}</p>
      <p className="text-faint">{c.disclaimer}</p>
    </div>
  );
}
