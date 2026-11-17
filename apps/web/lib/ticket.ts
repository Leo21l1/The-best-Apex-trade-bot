/** Pure order-ticket arithmetic (unit-tested). Prices in points; money in dollars. */
export interface TicketMath {
  riskTicks: number | null;
  rewardTicks: number | null;
  riskDollars: number | null;
  rewardDollars: number | null;
  rr: number | null;
  error: string | null;
}

export function ticketMath(
  direction: "long" | "short", entry: number | null, stop: number | null, target: number | null,
  qty: number, tick: number, tickValue: number,
): TicketMath {
  const none: TicketMath = { riskTicks: null, rewardTicks: null, riskDollars: null, rewardDollars: null, rr: null, error: null };
  if (entry == null || stop == null || !(qty > 0)) return none;
  const s = direction === "long" ? 1 : -1;
  if ((entry - stop) * s <= 0) return { ...none, error: direction === "long" ? "Stop must be below entry" : "Stop must be above entry" };
  if (target != null && (target - entry) * s <= 0) return { ...none, error: direction === "long" ? "Target must be above entry" : "Target must be below entry" };
  const riskTicks = Math.round(Math.abs(entry - stop) / tick);
  const rewardTicks = target != null ? Math.round(Math.abs(target - entry) / tick) : null;
  return {
    riskTicks, rewardTicks,
    riskDollars: riskTicks * tickValue * qty,
    rewardDollars: rewardTicks != null ? rewardTicks * tickValue * qty : null,
    rr: rewardTicks != null ? rewardTicks / riskTicks : null,
    error: null,
  };
}

export function unrealized(qty: number, avg: number, last: number | null, pointValue: number): number | null {
  return last == null ? null : (last - avg) * qty * pointValue;
}
