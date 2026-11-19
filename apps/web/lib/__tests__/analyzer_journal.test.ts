import { describe, expect, it } from "vitest";
import { ageText, analyzerShapes, countdown, DEFAULT_OVERLAYS, evidenceText, headline, readiness, snapTime, type Analysis } from "../analyzer";
import { etLocalToIso, previewPnl } from "../journal_calc";

describe("analyzer helpers", () => {
  it("snaps an ISO time to the latest bar at or before it", () => {
    const t = [100, 160, 220];
    expect(snapTime(t, new Date(170_000).toISOString())).toBe(160);
    expect(snapTime(t, new Date(50_000).toISOString())).toBeNull();
  });
  it("formats ages and countdowns", () => {
    expect(ageText(null)).toBe("no data");
    expect(ageText(30)).toBe("30s old");
    expect(ageText(600)).toBe("10m old");
    expect(countdown(95)).toBe("in 1h 35m");
    expect(countdown(-7)).toBe("7m ago");
    expect(countdown(0.2)).toBe("now");
  });
  it("draws only bounded, still-valid zones and the chosen overlays", () => {
    const zone = (i: number, status = "unmitigated") => ({ id: `z${i}`, type: "bullish_fvg", direction: "bullish", layer: "", formed_at: new Date(200_000).toISOString(), confirmed_at: new Date(260_000).toISOString(), top: 101 + i, bottom: 100 + i, status });
    const a = {
      data: { price: 100 }, levels: [
        { type: "liquidity", label: "Previous day high", short: "PDH", price: 110, timeframe: "1d", side: "buy-side", state: "untouched", valid: true, distance: 10 },
        { type: "liquidity", label: "Overnight low", short: "ONL", price: 90, timeframe: "session", side: "sell-side", state: "untouched", valid: true, distance: -10 },
        { type: "liquidity", label: "PWH", short: "PWH", price: 120, timeframe: "1w", side: "buy-side", state: "accepted beyond", valid: false, distance: 20 },
      ],
      advanced: { "5m": { fvgs: [...Array.from({ length: 8 }, (_, i) => zone(i)), zone(99, "invalidated")], order_blocks: [], internal: { events: [] }, swing: { events: [{ time: new Date(300_000).toISOString(), pivot_time: new Date(200_000).toISOString(), direction: "bullish", kind: "BOS", level: 105, layer: "swing" }] } } },
    } as unknown as Analysis;
    const times = [100, 200, 300, 400];
    const out = analyzerShapes(a, "5m", times, DEFAULT_OVERLAYS);
    expect(out.zones).toHaveLength(4);
    expect(out.zones.every((z) => z.id !== "z99")).toBe(true);
    expect(out.segments.map((s) => s.label)).toEqual(["PDH", "BOS"]); // session levels off by default; invalid PWH hidden
    expect(out.markers[0]?.text).toBe("BOS");
    const none = analyzerShapes(a, "5m", times, { fvg: false, ob: false, liquidity: false, structure: false, session: true });
    expect(none.zones).toHaveLength(0);
    expect(none.segments.map((s) => s.label)).toEqual(["ONL"]);
  });
});

describe("signal labels", () => {
  it("shows BUY / SELL / WAIT headlines and never hides an unvalidated strategy", () => {
    expect(headline("TECHNICAL BUY SETUP CONFIRMED")).toMatchObject({ text: "TECHNICAL BUY" });
    expect(headline("TECHNICAL SELL SETUP CONFIRMED").tone).toContain("text-down");
    expect(headline("DEVELOPING SELL").text).toBe("DEVELOPING SELL");
    expect(headline("EXPIRED").text).toBe("EXPIRED");
    expect(headline("WAIT").text).toBe("WAIT");
    expect(evidenceText("VALIDATED")).toEqual({ text: "VALIDATED", warn: false });
    expect(evidenceText("PROMISING BUT UNVALIDATED").text).toContain("RESEARCH / UNVALIDATED");
    expect(evidenceText(undefined).warn).toBe(true);
    // Technical detection is never presented as trade readiness without validated evidence.
    expect(readiness("PROMISING BUT UNVALIDATED")).toEqual({ text: "NOT VALIDATED · NOT TRADE-READY", warn: true });
    expect(readiness(undefined).warn).toBe(true);
    expect(readiness("VALIDATED").text).toContain("NOT A REAL-MONEY APPROVAL");
  });
});

describe("journal math", () => {
  it("handles scaled entries, partial exits and fees with contract point values", () => {
    const fills = [
      { time: "", side: "entry" as const, qty: 2, price: 20000 },
      { time: "", side: "entry" as const, qty: 1, price: 20003 },
      { time: "", side: "exit" as const, qty: 2, price: 20010 },
      { time: "", side: "exit" as const, qty: 1, price: 19995 },
    ];
    const p = previewPnl("MNQ", "long", fills, 3.6, 19990);
    expect(p.balanced).toBe(true);
    expect(p.avgEntry).toBe(20001);
    expect(p.gross).toBe(24);
    expect(p.net).toBe(20.4);
    expect(p.r).toBe(0.31); // 20.4 / (11 pts x $2 x 3 = $66)
    expect(previewPnl("ES", "short", [{ time: "", side: "entry", qty: 1, price: 5000 }, { time: "", side: "exit", qty: 1, price: 4990 }], 5).gross).toBe(500);
    expect(previewPnl("NQ", "long", [{ time: "", side: "entry", qty: 1, price: 18000 }, { time: "", side: "exit", qty: 1, price: 18010 }], 0).gross).toBe(200);
    expect(previewPnl("ES", "long", fills.slice(0, 3), 0).error).toMatch(/must equal/);
    expect(previewPnl("ES", "long", [{ time: "", side: "entry", qty: 1, price: 5000.1 }, { time: "", side: "exit", qty: 1, price: 5001 }], 0).error).toMatch(/tick grid/);
  });
  it("converts Eastern wall time to UTC across daylight saving", () => {
    expect(etLocalToIso("2026-11-18T09:30")).toBe("2026-11-18T14:30:00.000Z");
    expect(etLocalToIso("2026-07-01T09:30")).toBe("2026-07-01T13:30:00.000Z");
    expect(etLocalToIso("bad")).toBeNull();
  });
});
