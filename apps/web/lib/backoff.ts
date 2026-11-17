/** Capped exponential backoff with deterministic jitter injection for tests. */
export function backoffDelay(
  attempt: number,
  baseMs = 500,
  maxMs = 15_000,
  random: () => number = Math.random,
): number {
  const exp = Math.min(maxMs, baseMs * 2 ** Math.max(0, attempt));
  return Math.round(exp / 2 + random() * (exp / 2));
}
