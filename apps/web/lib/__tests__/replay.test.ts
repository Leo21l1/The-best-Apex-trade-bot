import type { ReplayData } from "@apex/shared-types";
import { describe, expect, it } from "vitest";
import { entryIndex, replayView } from "../replay";

const T = 1_794_495_600;
const data: ReplayData = {
  trade_id: 1, symbol: "ES", provider: "simulated",
  bars: Array.from({ length: 10 }, (_, k) => ({ t: T + k * 60, o: 1, h: 1, l: 1, c: 1, v: 1 })),
  entry: { t: T + 3 * 60, price: 1, direction: "long" }, exit: { t: T + 6 * 60 + 30, price: 2 },
  stop: 0.5, target: 3, net_pnl: 10,
  signal_at_entry: { label: "BUY" }, market_at_entry: { regime: "range" },
  events: [
    { t: T + 8 * 60, known_from: T - 86400, title: "CPI (scheduled)", importance: "high", affected: ["ES"] },
    { t: T + 5 * 60, known_from: T + 5 * 60, title: "Surprise", importance: "high", affected: ["ES"] },
  ],
  hindsight: null, scenarios: null,
};

describe("replayView", () => {
  it("never reveals future bars, unannounced events or entry context early", () => {
    const v = replayView(data, 2);
    expect(v.bars).toHaveLength(3);
    expect(v.afterEntry).toBe(false);
    expect(v.signal).toBeNull();
    expect(v.market).toBeNull();
    expect(v.knownEvents.map((e) => e.title)).toEqual(["CPI (scheduled)"]); // scheduled = known in advance
    expect(v.pastEvents).toEqual([]);
  });
  it("reveals each item exactly when it became available", () => {
    expect(replayView(data, 3).signal).toEqual({ label: "BUY" });
    expect(replayView(data, 4).knownEvents.map((e) => e.title)).toEqual(["CPI (scheduled)", "Surprise"]);
    expect(replayView(data, 5).inTrade).toBe(true);
    expect(replayView(data, 5).exited).toBe(false);
    expect(replayView(data, 6).exited).toBe(true);
    expect(replayView(data, 9).finished).toBe(true);
    expect(replayView(data, 99).bars).toHaveLength(10);
    expect(entryIndex(data)).toBe(3);
  });
});
