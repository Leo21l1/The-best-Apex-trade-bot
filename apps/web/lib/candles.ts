import type { Bar, Timeframe } from "@apex/shared-types";

export const TIMEFRAMES: Timeframe[] = ["1m", "3m", "5m", "15m", "30m", "1h", "4h", "1d", "1w"];

export const TF_SECONDS: Record<Timeframe, number> = {
  "1m": 60,
  "3m": 180,
  "5m": 300,
  "15m": 900,
  "30m": 1800,
  "1h": 3600,
  "4h": 14400,
  "1d": 86400,
  "1w": 604800,
};

/**
 * Timeframes whose buckets align with UTC clock boundaries (New York is always a whole number
 * of hours from UTC, and server buckets are anchored to the 18:00 ET session open). Larger
 * timeframes depend on the trading-day calendar and are only ever computed by the server.
 */
export function isClockAligned(tf: Timeframe): boolean {
  return TF_SECONDS[tf] <= 3600;
}

export function bucketStart(t: number, tf: Timeframe): number {
  const size = TF_SECONDS[tf];
  return Math.floor(t / size) * size;
}

/**
 * Fold a live 1-minute update into a bar series of any timeframe. Pure; returns a new array.
 *
 * - 1m: replace or append the bar with the same open time.
 * - clock-aligned timeframes: update the current bucket's H/L/C, or open a new bucket.
 * - 4h/1d/1w: only extend the last bar's H/L/C (the server defines those buckets).
 *
 * Volume of aggregated bars is intentionally not adjusted here; the caller refreshes the tail
 * from the server whenever a 1-minute bar closes, which is the authoritative aggregation.
 * Out-of-order updates older than the last bar are ignored.
 */
export function applyLiveUpdate(bars: Bar[], u: Bar, tf: Timeframe): Bar[] {
  const last = bars[bars.length - 1];
  if (tf === "1m") {
    if (!last || u.t > last.t) return [...bars, pick(u)];
    if (u.t === last.t) return [...bars.slice(0, -1), pick(u)];
    return bars;
  }
  if (!last) return isClockAligned(tf) ? [{ ...pick(u), t: bucketStart(u.t, tf) }] : bars;
  if (u.t < last.t) return bars;
  if (isClockAligned(tf)) {
    const bucket = bucketStart(u.t, tf);
    if (bucket > last.t) return [...bars, { ...pick(u), t: bucket }];
  }
  const merged: Bar = { ...last, h: Math.max(last.h, u.h), l: Math.min(last.l, u.l), c: u.c };
  return [...bars.slice(0, -1), merged];
}

/** Merge server bars into a series by open time (server wins), keeping ascending order. */
export function mergeBars(existing: Bar[], incoming: Bar[]): Bar[] {
  const map = new Map<number, Bar>();
  for (const b of existing) map.set(b.t, b);
  for (const b of incoming) map.set(b.t, b);
  return [...map.values()].sort((a, b) => a.t - b.t);
}

function pick(u: Bar): Bar {
  return { t: u.t, o: u.o, h: u.h, l: u.l, c: u.c, v: u.v };
}

/** Number of decimals needed to display prices on a tick grid (0.25 -> 2, 1 -> 0). */
export function tickDecimals(tick: number): number {
  const s = tick.toString();
  const i = s.indexOf(".");
  return i === -1 ? 0 : s.length - i - 1;
}

export function formatPrice(value: number, tick: number): string {
  const d = tickDecimals(tick);
  return value.toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
}
