import { describe, expect, it } from "vitest";
import { ticketMath, unrealized } from "../ticket";

describe("ticket math", () => {
  it("computes risk, reward and R:R for ES", () => {
    const m = ticketMath("long", 5000, 4996, 5008, 2, 0.25, 12.5);
    expect(m).toEqual({ riskTicks: 16, rewardTicks: 32, riskDollars: 400, rewardDollars: 800, rr: 2, error: null });
  });
  it("handles shorts, micros and missing target", () => {
    const m = ticketMath("short", 18000, 18010, null, 3, 0.25, 0.5);
    expect(m.riskTicks).toBe(40);
    expect(m.riskDollars).toBe(60);
    expect(m.rr).toBeNull();
  });
  it("rejects stops and targets on the wrong side", () => {
    expect(ticketMath("long", 5000, 5001, null, 1, 0.25, 12.5).error).toBe("Stop must be below entry");
    expect(ticketMath("short", 5000, 4999, null, 1, 0.25, 12.5).error).toBe("Stop must be above entry");
    expect(ticketMath("long", 5000, 4990, 4995, 1, 0.25, 12.5).error).toBe("Target must be above entry");
    expect(ticketMath("long", null, 4990, null, 1, 0.25, 12.5).riskTicks).toBeNull();
  });
  it("unrealized P&L", () => {
    expect(unrealized(2, 5000, 5004, 50)).toBe(400);
    expect(unrealized(-1, 70.5, 70.25, 1000)).toBe(250);
    expect(unrealized(1, 5000, null, 50)).toBeNull();
  });
});
