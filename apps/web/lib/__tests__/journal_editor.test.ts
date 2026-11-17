import type { JournalTrade } from "@apex/shared-types";
import { describe, expect, it } from "vitest";
import { diff, fromNyLocal, toNyLocal } from "@/features/journal/TradeEditor";

const T: JournalTrade = {
  id: 1, source: "csv", trading_date: "2026-11-11", symbol: "MES", contract: "MESZ6", direction: "long", quantity: 2,
  entry_time: "2026-11-11T15:00:00+00:00", exit_time: "2026-11-11T15:05:00+00:00", entry_price: 5000, exit_price: 5004,
  gross_pnl: 40, fees: 2.4, net_pnl: 37.6, strategy_id: null, planned_stop: null, planned_target: null, followed_plan: null,
  mistakes: [], notes: null, emotion_tags: [], tags: [], chart_links: [],
};

function form(over: Record<string, unknown> = {}) {
  return {
    trading_date: "2026-11-11", entry_time: toNyLocal(T.entry_time), exit_time: toNyLocal(T.exit_time), symbol: "MES",
    contract: "MESZ6", direction: "long", quantity: "2", entry_price: "5000", exit_price: "5004", fees: "2.4", net_pnl: "37.6",
    planned_stop: "", planned_target: "", strategy_id: "", setup: "", followed_plan: "", thesis: "", psychology: "",
    emotion_tags: "", lessons: "", tags: "", chart_links: "", mistakes: [] as string[], notes: "", ...over,
  };
}

describe("journal editor", () => {
  it("converts New York wall time both ways, across daylight saving", () => {
    expect(toNyLocal("2026-11-11T15:00:00Z")).toBe("2026-11-11T10:00:00"); // EST
    expect(toNyLocal("2026-07-15T14:00:00Z")).toBe("2026-07-15T10:00:00"); // EDT
    expect(fromNyLocal("2026-11-11T10:00:00")).toBe("2026-11-11T15:00:00.000Z");
    expect(fromNyLocal("2026-07-15T10:00")).toBe("2026-07-15T14:00:00.000Z");
    expect(fromNyLocal("2026-03-09T09:30")).toBe("2026-03-09T13:30:00.000Z"); // day after spring forward
  });

  it("sends only changed fields", () => {
    expect(diff(T, form())).toEqual({});
    expect(diff(T, form({ setup: "ORB", tags: "b, a, a" }))).toEqual({ setup: "ORB", tags: ["a", "b"] });
  });

  it("lets the server recompute P&L when execution inputs change", () => {
    const d = diff(T, form({ quantity: "3" }));
    expect(d).toEqual({ quantity: 3 });
    expect(diff(T, form({ net_pnl: "50" }))).toEqual({ net_pnl: 50 });
  });

  it("converts edited times to UTC", () => {
    expect(diff(T, form({ entry_time: "2026-11-11T10:01:00" }))).toEqual({ entry_time: "2026-11-11T15:01:00.000Z" });
  });
});
