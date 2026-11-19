/** Types and pure helpers for the Market Analyzer (deterministic; unit-tested). */

export interface DataStatus {
  label: string; available: boolean; provider: string; quality: string; price: number | null;
  price_time: string | null; age_seconds: number | null; stale: boolean; market_open: boolean;
  contract: string | null; note: string | null; confidence?: string; confidence_note?: string;
  change?: number; change_pct?: number | null; prev_session?: string; prev_close?: number;
}
export interface Bias {
  direction: "BULLISH" | "BEARISH" | "NEUTRAL" | "WAIT"; label: string; sentence: string;
  htf_1h?: string; htf_basis?: string; swing_15m?: string; short_term_5m?: string; pullback?: boolean;
}
export interface Level {
  type: "liquidity" | "zone"; label: string; short?: string; price: number; top?: number; bottom?: number;
  timeframe: string; side?: string; state: string; state_at?: string | null; valid: boolean; formed?: string;
  invalidation?: number; distance: number;
}
export interface NewsBrief { status: string | null; summary: string | null }
export interface Card {
  symbol: string; name: string; data: DataStatus; session: { name: string; market_open: boolean }; bias: Bias;
  headline_structure: string | null; liquidity: { above: Level | null; below: Level | null };
  setup: { strategy: string; direction: string } | null; quality: number | null; news: NewsBrief;
  state: string; confluence: { score: number; out_of: number; direction: string | null } | null;
  last_confirmation: string | null;
}
export interface Overview { as_of: string; cards: Card[]; note: string; disclaimer: string }
export interface Factor { n: number; name: string; status: "PASS" | "FAIL" | "UNKNOWN"; detail: string; event?: string | null }
export interface Rr { gross: number | null; net: number | null }
export interface Candidate {
  strategy_id: string; direction: "long" | "short"; score: number; factors: Factor[]; mandatory: string[];
  requirements: { key: string; ok: boolean; detail: string }[]; missing: string[]; confirmed: boolean; evidence: string;
  trigger_at: string | null;
  levels: { entry: number; entry_zone: [number, number]; stop: number | null; stop_basis: string | null; tp1: number | null;
    tp1_basis: string | null; tp2: number | null; tp2_basis: string | null; rr_tp1: Rr | null; rr_tp2: Rr | null;
    risk_usd_1c?: number | null; risk_warning?: string | null };
}
export interface Recorded {
  id: number; strategy_id: string; direction: "long" | "short"; score: number; evidence: string; contract: string | null;
  confirmed_at: string; confirmed_at_et: string; expires_at: string; expires_at_et: string; entry: number;
  entry_zone: [number, number]; stop: number; tp1: number; tp2: number | null; net_rr_tp1: number | null; rr_tp2: Rr | null;
  stop_basis: string | null; tp1_basis: string | null; tp2_basis: string | null; factors: Factor[]; status: string;
  status_reason: string | null; filled_at: string | null; display_state: string; entered: boolean; engine_version: string;
  risk_usd_1c?: number | null; risk_warning?: string | null;
}
export interface SignalState {
  state: string; display_state?: string; best: Candidate | null; candidates: Candidate[]; factors: Factor[];
  direction_scores?: Record<string, number>; recorded?: Recorded | null; note?: string; reason?: string;
}
export interface StrategyRow { id: string; name: string; status: string; detail: string; side?: string | null; missing?: string[] }
export interface Setup {
  strategy: string; strategy_id: string; direction: string; detected_at: string; satisfied: string[]; missing: string[];
  entry_area: (number | null)[]; entry: number; stop: number; targets: number[];
  rr_t1: { gross: number | null; net: number | null } | null; rr_t2: { gross: number | null; net: number | null } | null;
  risk_1c_usd: number | null; evidence: { evidence?: string; reason?: string; available?: boolean; wf_trades?: number };
  aligned_with_bias: boolean; note: string;
}
export interface StructureEvent { time: string; direction: string; kind: string; level: number; layer: string; pivot_time: string }
export interface Zone { id: string; type: string; direction: string; layer: string; formed_at: string; confirmed_at: string; top: number; bottom: number; status: string }
export interface TfSummary {
  available: boolean; internal_trend: string | null; swing_trend: string | null;
  last_internal: StructureEvent | null; last_swing: StructureEvent | null;
  premium_discount: { zone: string; position: number; range_high: number; range_low: number } | null;
  extremes: { top: number; top_label: string; bottom: number; bottom_label: string } | null; bars: number; reason?: string;
}
export interface Analysis {
  symbol: string; name: string; tick_size: number; point_value: number; as_of: string; data: DataStatus;
  session: { name: string; market_open: boolean }; news: { status: string; summary: string; next_high_impact: { title: string; time_et: string; minutes_until: number } | null } | null;
  bias: Bias; headline_structure: string | null; observations: string[]; levels: Level[];
  liquidity: { above: Level | null; below: Level | null }; setup: Setup | null; why_wait: string[];
  timeframes: Record<string, TfSummary>; confluence: { score: number; out_of: number; label: string; direction: string | null; factors: Factor[]; threshold: number } | null;
  signal: SignalState;
  strategies: Record<string, StrategyRow[]>; narrative: string; disclaimer: string;
  advanced: Record<string, { internal?: { events: StructureEvent[] }; swing?: { events: StructureEvent[] }; order_blocks?: Zone[]; fvgs?: Zone[] }>;
}

export const BIAS_TONE: Record<string, string> = {
  BULLISH: "text-up border-up/40 bg-up/10",
  BEARISH: "text-down border-down/40 bg-down/10",
  NEUTRAL: "text-muted border-line bg-panel-2",
  WAIT: "text-warn border-warn/40 bg-warn/10",
};

export const NEWS_TONE: Record<string, string> = {
  "NEWS WINDOW ACTIVE": "text-down",
  "POST-NEWS VOLATILITY": "text-warn",
  "HIGH IMPACT UPCOMING": "text-warn",
  "NEWS DATA STALE / UNAVAILABLE": "text-warn",
  "NO MAJOR SCHEDULED EVENT NEARBY": "text-up",
};

export function ageText(seconds: number | null): string {
  if (seconds == null) return "no data";
  if (seconds < 90) return `${Math.max(0, Math.round(seconds))}s old`;
  if (seconds < 5400) return `${Math.round(seconds / 60)}m old`;
  if (seconds < 172800) return `${Math.round(seconds / 3600)}h old`;
  return `${Math.round(seconds / 86400)}d old`;
}

export function fmtPrice(p: number | null | undefined, tick = 0.25): string {
  if (p == null || Number.isNaN(p)) return "—";
  const dp = tick < 0.01 ? 4 : tick < 1 ? 2 : 0;
  return p.toLocaleString("en-US", { minimumFractionDigits: dp, maximumFractionDigits: dp });
}

/** Latest bar time (epoch seconds) at or before ``iso``; null when it precedes the loaded bars. */
export function snapTime(times: number[], iso: string): number | null {
  const t = Date.parse(iso) / 1000;
  let lo = 0, hi = times.length - 1, ans: number | null = null;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (times[mid]! <= t) { ans = times[mid]!; lo = mid + 1; } else hi = mid - 1;
  }
  return ans;
}

export interface OverlayOptions { fvg: boolean; ob: boolean; liquidity: boolean; structure: boolean; session: boolean }
export const DEFAULT_OVERLAYS: OverlayOptions = { fvg: true, ob: true, liquidity: true, structure: true, session: false };

const UP = "rgba(52,211,153,", DOWN = "rgba(240,90,94,";

/** Chart shapes for the most important ACTIVE items only (bounded counts so the chart stays clean). */
export function analyzerShapes(a: Analysis, tf: string, times: number[], o: OverlayOptions, maxZones = 4) {
  const zones: { id: string; t1: number; t2: number | null; top: number; bottom: number; fill: string; stroke: string; dashed?: boolean; label?: string }[] = [];
  const segments: { id: string; t1: number; t2: number; price: number; color: string; dashed?: boolean; label?: string }[] = [];
  const markers: { id: string; t: number; text: string; color: string; position: "aboveBar" | "belowBar"; shape: "circle" | "square" | "arrowUp" | "arrowDown" }[] = [];
  if (!times.length) return { zones, segments, markers };
  const last = times[times.length - 1]!;
  const adv = a.advanced[tf] ?? {};
  const price = a.data.price ?? 0;
  const pick = (zs: Zone[] | undefined) => (zs ?? [])
    .filter((z) => z.status !== "invalidated" && z.status !== "fully mitigated")
    .sort((x, y) => Math.abs((x.top + x.bottom) / 2 - price) - Math.abs((y.top + y.bottom) / 2 - price))
    .slice(0, maxZones);
  const addZone = (z: Zone, kind: string) => {
    const t1 = snapTime(times, z.formed_at);
    if (t1 == null) return;
    const c = z.direction === "bullish" ? UP : DOWN;
    zones.push({ id: z.id, t1, t2: null, top: z.top, bottom: z.bottom, fill: `${c}0.10)`, stroke: `${c}0.55)`, dashed: kind === "FVG", label: `${kind}${z.layer ? ` ${z.layer[0]!.toUpperCase()}` : ""}` });
  };
  if (o.fvg) pick(adv.fvgs).forEach((z) => addZone(z, "FVG"));
  if (o.ob) pick(adv.order_blocks).forEach((z) => addZone(z, "OB"));
  const first = times[0]!;
  for (const lv of a.levels) {
    if (lv.type !== "liquidity" || !lv.valid) continue;
    const isSession = lv.timeframe === "session";
    if ((isSession && !o.session) || (!isSession && !o.liquidity)) continue;
    segments.push({ id: `${lv.label}-${lv.price}`, t1: first, t2: last, price: lv.price, color: lv.side === "buy-side" ? `${DOWN}0.7)` : `${UP}0.7)`, dashed: true, label: lv.short ?? lv.label });
  }
  if (o.structure) {
    const evs = [...(adv.swing?.events ?? []).slice(-3), ...(adv.internal?.events ?? []).slice(-3)];
    for (const e of evs) {
      const t = snapTime(times, e.time);
      const t0 = snapTime(times, e.pivot_time);
      if (t == null) continue;
      const color = e.direction === "bullish" ? `${UP}0.9)` : `${DOWN}0.9)`;
      segments.push({ id: `${e.layer}-${e.time}`, t1: t0 ?? t, t2: t, price: e.level, color, dashed: e.layer === "internal", label: `${e.layer === "internal" ? "i" : ""}${e.kind}` });
      markers.push({ id: `m-${e.layer}-${e.time}`, t, text: `${e.layer === "internal" ? "i" : ""}${e.kind}`, color, position: e.direction === "bullish" ? "belowBar" : "aboveBar", shape: e.direction === "bullish" ? "arrowUp" : "arrowDown" });
    }
  }
  return { zones, segments: segments.slice(0, 14), markers: markers.sort((x, y) => x.t - y.t) };
}

export function countdown(minutes: number): string {
  const m = Math.round(minutes);
  if (m === 0) return "now";
  const abs = Math.abs(m), h = Math.floor(abs / 60), r = abs % 60;
  const s = h ? `${h}h ${r}m` : `${r}m`;
  return m > 0 ? `in ${s}` : `${s} ago`;
}

export const STRATEGY_NAME: Record<string, string> = {
  mtf_trend: "Multi-Timeframe Trend", vwap_trend: "VWAP Trend", momentum_pullback: "Momentum Pullback",
  orb: "Opening Range Breakout", breakout_retest: "Breakout Retest", vol_expansion: "Volatility Expansion",
  sweep_reversal: "Liquidity Sweep Reversal", mean_reversion: "Mean Reversion", smc_sweep_mss: "Sweep + Market Structure Shift",
  smc_ob_retest: "Order Block Retest", smc_fvg_retest: "Fair Value Gap Retest",
};

/** Short card headline: BUY SETUP / SELL SETUP / DEVELOPING ... / WAIT (and ended states). */
export function headline(state: string): { text: string; tone: string } {
  if (state.includes("BUY SETUP CONFIRMED")) return { text: "BUY SETUP", tone: "text-up border-up/50 bg-up/10" };
  if (state.includes("SELL SETUP CONFIRMED")) return { text: "SELL SETUP", tone: "text-down border-down/50 bg-down/10" };
  if (state === "DEVELOPING BUY") return { text: "DEVELOPING BUY", tone: "text-up/80 border-up/30" };
  if (state === "DEVELOPING SELL") return { text: "DEVELOPING SELL", tone: "text-down/80 border-down/30" };
  if (["INVALIDATED", "EXPIRED", "MISSED"].includes(state)) return { text: state, tone: "text-warn border-warn/40" };
  return { text: "WAIT", tone: "text-muted border-line bg-panel-2" };
}

export function evidenceText(label: string | undefined): { text: string; warn: boolean } {
  if (label === "VALIDATED") return { text: "VALIDATED", warn: false };
  return { text: `${label ?? "INSUFFICIENT EVIDENCE"} · RESEARCH / UNVALIDATED`, warn: true };
}
