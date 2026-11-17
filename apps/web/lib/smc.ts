import type { SmcPattern } from "@apex/shared-types";

export interface SmcToggles {
  sweeps: boolean;
  orderBlocks: boolean;
  fvg: boolean;
  structure: boolean;
  supplyDemand: boolean;
  equalLevels: boolean;
  showInvalidated: boolean;
}

export const DEFAULT_SMC: SmcToggles = {
  sweeps: true, orderBlocks: true, fvg: true, structure: true, supplyDemand: false, equalLevels: false, showInvalidated: false,
};

export const SMC_LABELS: Record<keyof Omit<SmcToggles, "showInvalidated">, string> = {
  sweeps: "Sweeps", orderBlocks: "Order blocks", fvg: "FVG", structure: "BOS / CHoCH", supplyDemand: "Supply/Demand", equalLevels: "EQH/EQL",
};

export interface ZoneOut { id: string; t1: number; t2: number | null; top: number; bottom: number; fill: string; stroke: string; dashed?: boolean; label?: string }
export interface SegmentOut { id: string; t1: number; t2: number; price: number; color: string; dashed?: boolean; label?: string; width?: number }
export interface MarkerOut { id: string; t: number; text: string; color: string; position: "aboveBar" | "belowBar"; shape: "circle" | "square" | "arrowUp" | "arrowDown" }

const C = {
  sweep: "#facc15",
  bullOb: ["rgba(52,211,153,0.16)", "rgba(52,211,153,0.7)"],
  bearOb: ["rgba(240,90,94,0.16)", "rgba(240,90,94,0.7)"],
  bullFvg: ["rgba(52,211,153,0.08)", "rgba(52,211,153,0.45)"],
  bearFvg: ["rgba(240,90,94,0.08)", "rgba(240,90,94,0.45)"],
  demand: ["rgba(56,189,248,0.10)", "rgba(56,189,248,0.6)"],
  supply: ["rgba(245,158,11,0.10)", "rgba(245,158,11,0.6)"],
  bos: "#3b82f6",
  choch: "#a855f7",
  eq: "#94a3b8",
} as const;

export function familyOf(type: string): keyof Omit<SmcToggles, "showInvalidated"> | null {
  if (type.endsWith("_sweep")) return "sweeps";
  if (type.endsWith("order_block")) return "orderBlocks";
  if (type.endsWith("fvg")) return "fvg";
  if (type === "BOS" || type === "CHoCH" || type === "MSS") return "structure";
  if (type === "demand_zone" || type === "supply_zone") return "supplyDemand";
  if (type === "equal_highs" || type === "equal_lows") return "equalLevels";
  return null;
}

/** Open time (seconds) of the last bar at or before ``iso``; null if before the first bar. */
export function snap(times: number[], iso: string | null): number | null {
  if (!iso || !times.length) return null;
  const t = Math.floor(new Date(iso).getTime() / 1000);
  if (t < times[0]!) return null;
  let lo = 0, hi = times.length - 1;
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (times[mid]! <= t) lo = mid; else hi = mid - 1;
  }
  return times[lo]!;
}

/**
 * Turn SMC patterns into chart shapes for the bars currently on screen. Patterns are filtered by
 * toggle family, status, minimum quality and a per-family cap (most recent first).
 */
export function patternShapes(
  patterns: SmcPattern[],
  times: number[],
  toggles: SmcToggles,
  opts: { minQuality?: number; maxPerFamily?: number; selected?: string | null } = {},
): { zones: ZoneOut[]; segments: SegmentOut[]; markers: MarkerOut[] } {
  const zones: ZoneOut[] = [], segments: SegmentOut[] = [], markers: MarkerOut[] = [];
  const counts: Record<string, number> = {};
  const max = opts.maxPerFamily ?? 8;
  const step = times.length > 1 ? times[1]! - times[0]! : 60;
  for (const p of patterns) {
    const fam = familyOf(p.type);
    if (!fam || !toggles[fam]) continue;
    if (!toggles.showInvalidated && p.status === "invalidated") continue;
    if (p.quality < (opts.minQuality ?? 0) && p.id !== opts.selected) continue;
    counts[fam] = (counts[fam] ?? 0) + 1;
    if (counts[fam]! > max && p.id !== opts.selected) continue;
    const t1 = snap(times, p.formed_at);
    const tc = snap(times, p.confirmed_at);
    if (t1 === null) continue;
    const sel = p.id === opts.selected;
    const bull = p.direction === "bullish";
    const faded = p.status === "invalidated";
    if (fam === "orderBlocks" || fam === "fvg" || fam === "supplyDemand") {
      const [fill, stroke] = fam === "orderBlocks" ? (bull ? C.bullOb : C.bearOb)
        : fam === "fvg" ? (bull ? C.bullFvg : C.bearFvg)
          : (bull ? C.demand : C.supply);
      const short = fam === "orderBlocks" ? "OB" : fam === "fvg" ? (p.type === "inverse_fvg" ? "IFVG" : "FVG") : bull ? "DEMAND" : "SUPPLY";
      zones.push({
        id: p.id, t1, t2: faded ? (tc ?? t1) + step * 10 : null, top: p.top, bottom: p.bottom,
        fill: faded ? "rgba(139,147,158,0.06)" : fill, stroke: sel ? "#38bdf8" : faded ? "rgba(139,147,158,0.4)" : stroke,
        dashed: fam === "fvg" || faded, label: sel || p.status === "active" || p.status === "open" ? short : undefined,
      });
    } else if (fam === "sweeps") {
      const lvl = Number(p.extra.level ?? (p.direction === "bearish" ? p.bottom : p.top));
      markers.push({ id: p.id, t: t1, text: p.status === "confirmed_rejection" ? "SWEEP ✓" : "sweep", color: C.sweep, position: p.type === "buy_side_sweep" ? "aboveBar" : "belowBar", shape: "circle" });
      segments.push({ id: p.id, t1: t1 - step * 12, t2: (tc ?? t1) + step * 2, price: lvl, color: C.sweep, dashed: true, width: sel ? 2.5 : 1.5, label: sel ? String(p.extra.level_name ?? "") : undefined });
    } else if (fam === "structure") {
      const color = p.type === "BOS" ? C.bos : C.choch;
      if (tc !== null) segments.push({ id: p.id, t1, t2: tc, price: p.top, color, width: sel ? 2.5 : 1.5, label: p.type });
    } else if (fam === "equalLevels" && tc !== null) {
      segments.push({ id: p.id, t1, t2: tc + step * 20, price: p.top, color: C.eq, dashed: true, label: p.type === "equal_highs" ? "EQH" : "EQL" });
    }
  }
  return { zones, segments, markers };
}

export const PATTERN_NAMES: Record<string, string> = {
  buy_side_sweep: "Buy-side liquidity sweep",
  sell_side_sweep: "Sell-side liquidity sweep",
  bullish_order_block: "Bullish order block",
  bearish_order_block: "Bearish order block",
  bullish_fvg: "Bullish fair value gap",
  bearish_fvg: "Bearish fair value gap",
  inverse_fvg: "Inverse fair value gap",
  BOS: "Break of structure",
  CHoCH: "Change of character",
  MSS: "Market structure shift",
  demand_zone: "Demand zone",
  supply_zone: "Supply zone",
  equal_highs: "Equal highs",
  equal_lows: "Equal lows",
  breakout_retest: "Breakout and retest",
  momentum_divergence: "Momentum divergence (RSI 14)",
};
