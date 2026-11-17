import type { SmcPattern } from "@apex/shared-types";
import { describe, expect, it } from "vitest";
import { DEFAULT_SMC, familyOf, patternShapes, snap } from "../smc";

const T0 = Date.UTC(2026, 10, 11, 15, 0) / 1000;
const times = Array.from({ length: 50 }, (_, i) => T0 + i * 300);
const iso = (i: number, extraSec = 0) => new Date((T0 + i * 300 + extraSec) * 1000).toISOString();

function pat(type: string, over: Partial<SmcPattern> = {}): SmcPattern {
  return {
    id: type, type, direction: "bullish", formed_at: iso(10), confirmed_at: iso(12), top: 101, bottom: 100,
    status: "active", invalidation: "", invalidation_price: 100, evidence: [], quality: 60, extra: {}, ...over,
  };
}

describe("smc shapes", () => {
  it("snaps timestamps to the containing bar and rejects times before the first bar", () => {
    expect(snap(times, iso(3, 120))).toBe(times[3]);
    expect(snap(times, iso(-1))).toBeNull();
    expect(snap(times, null)).toBeNull();
  });

  it("maps families", () => {
    expect(familyOf("buy_side_sweep")).toBe("sweeps");
    expect(familyOf("bearish_order_block")).toBe("orderBlocks");
    expect(familyOf("inverse_fvg")).toBe("fvg");
    expect(familyOf("MSS")).toBe("structure");
    expect(familyOf("momentum_divergence")).toBeNull();
  });

  it("draws order blocks as open-ended green zones and BOS as a line from swing to break", () => {
    const out = patternShapes([pat("bullish_order_block"), pat("BOS", { top: 102, bottom: 102 })], times, DEFAULT_SMC);
    expect(out.zones).toHaveLength(1);
    expect(out.zones[0]).toMatchObject({ t1: times[10], t2: null, top: 101, bottom: 100 });
    expect(out.zones[0]!.fill).toContain("52,211,153");
    expect(out.segments[0]).toMatchObject({ t1: times[10], t2: times[12], price: 102, label: "BOS" });
  });

  it("sweeps get a yellow marker and swept-level line", () => {
    const out = patternShapes([pat("buy_side_sweep", { direction: "bearish", status: "confirmed_rejection", extra: { level: 105, level_name: "swing high" } })], times, DEFAULT_SMC);
    expect(out.markers[0]).toMatchObject({ t: times[10], text: "SWEEP ✓", position: "aboveBar", color: "#facc15" });
    expect(out.segments[0]).toMatchObject({ price: 105, color: "#facc15" });
  });

  it("filters by toggle, invalidation, quality and per-family cap, but keeps the selected pattern", () => {
    const many = Array.from({ length: 5 }, (_, i) => pat("bullish_fvg", { id: `g${i}`, quality: 40 + i * 10 }));
    expect(patternShapes(many, times, { ...DEFAULT_SMC, fvg: false }).zones).toHaveLength(0);
    expect(patternShapes(many, times, DEFAULT_SMC, { maxPerFamily: 2 }).zones).toHaveLength(2);
    expect(patternShapes(many, times, DEFAULT_SMC, { minQuality: 70 }).zones.map((z) => z.id)).toEqual(["g3", "g4"]);
    expect(patternShapes(many, times, DEFAULT_SMC, { minQuality: 70, selected: "g0" }).zones.map((z) => z.id)).toContain("g0");
    const inv = [pat("bullish_order_block", { status: "invalidated" })];
    expect(patternShapes(inv, times, DEFAULT_SMC).zones).toHaveLength(0);
    expect(patternShapes(inv, times, { ...DEFAULT_SMC, showInvalidated: true }).zones[0]!.t2).not.toBeNull();
  });
});
