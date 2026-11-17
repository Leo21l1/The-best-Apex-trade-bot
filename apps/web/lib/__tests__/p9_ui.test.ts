import type { JournalTrade, RadarSymbol } from "@apex/shared-types";
import { describe, expect, it } from "vitest";
import { dailyPnl } from "@/features/journal/Journal";
import { alignment } from "@/features/radar/Radar";

describe("journal calendar aggregation", () => {
  it("sums net P&L per trading date and counts trades", () => {
    const t = (d: string, n: number) => ({ trading_date: d, net_pnl: n }) as JournalTrade;
    expect(dailyPnl([t("2026-11-12", -40.1), t("2026-11-11", 100), t("2026-11-12", 20.05)])).toEqual([
      { date: "2026-11-11", net: 100, trades: 1 },
      { date: "2026-11-12", net: -20.05, trades: 2 },
    ]);
  });
});

describe("radar alignment", () => {
  it("counts up minus down timeframes and ignores flat/missing", () => {
    const s = { timeframes: [{ trend: "up" }, { trend: "up" }, { trend: "down" }, { trend: null }, { trend: "flat" }] } as unknown as RadarSymbol;
    expect(alignment(s)).toBe(1);
  });
});
