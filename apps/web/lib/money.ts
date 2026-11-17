/** Display helpers for possibly-unknown money values. Unknown is shown as "Unknown", never 0. */
export function money(v: number | null | undefined, signed = false): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "Unknown";
  const abs = Math.abs(v).toLocaleString("en-US", { style: "currency", currency: "USD" });
  if (v < 0) return `-${abs}`;
  return signed && v > 0 ? `+${abs}` : abs;
}

/** Fraction of the way from `from` to `to`, clamped to [0, 1]. */
export function progress(value: number, from: number, to: number): number {
  if (to === from) return 0;
  return Math.min(1, Math.max(0, (value - from) / (to - from)));
}
