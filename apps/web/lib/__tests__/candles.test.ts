import type { Bar } from "@apex/shared-types";
import { describe, expect, it } from "vitest";
import {
  applyLiveUpdate,
  bucketStart,
  formatPrice,
  isClockAligned,
  mergeBars,
  tickDecimals,
} from "../candles";

const T = 1_794_495_600; // an exact UTC hour boundary
const bar = (t: number, o: number, h: number, l: number, c: number, v = 1): Bar => ({ t, o, h, l, c, v });

describe("bucketStart", () => {
  it("floors to the timeframe", () => {
    expect(T % 3600).toBe(0);
    expect(bucketStart(T + 299, "5m")).toBe(T);
    expect(bucketStart(T + 300, "5m")).toBe(T + 300);
    expect(bucketStart(T + 3599, "1h")).toBe(T);
    expect(isClockAligned("1h")).toBe(true);
    expect(isClockAligned("4h")).toBe(false);
  });
});

describe("applyLiveUpdate", () => {
  it("1m: replaces the forming bar and appends new ones", () => {
    let s = [bar(T, 10, 11, 9, 10)];
    s = applyLiveUpdate(s, bar(T, 10, 12, 9, 11.5, 5), "1m");
    expect(s).toEqual([bar(T, 10, 12, 9, 11.5, 5)]);
    s = applyLiveUpdate(s, bar(T + 60, 11.5, 12, 11, 11.75), "1m");
    expect(s.map((b) => b.t)).toEqual([T, T + 60]);
  });

  it("ignores out-of-order updates", () => {
    const s = [bar(T, 1, 1, 1, 1), bar(T + 60, 2, 2, 2, 2)];
    expect(applyLiveUpdate(s, bar(T, 9, 9, 9, 9), "1m")).toBe(s);
    expect(applyLiveUpdate(s, bar(T - 600, 9, 9, 9, 9), "5m")).toBe(s);
  });

  it("5m: extends the current bucket without touching the open", () => {
    const s = [bar(T, 100, 101, 99, 100.5, 40)];
    const out = applyLiveUpdate(s, bar(T + 120, 100.5, 103, 98, 102, 7), "5m");
    expect(out).toEqual([bar(T, 100, 103, 98, 102, 40)]);
    expect(s[0]!.h).toBe(101); // input not mutated
  });

  it("5m: opens a new bucket at the boundary", () => {
    const s = [bar(T, 100, 101, 99, 100.5)];
    const out = applyLiveUpdate(s, bar(T + 300, 100.5, 100.75, 100.25, 100.5, 3), "5m");
    expect(out).toHaveLength(2);
    expect(out[1]).toEqual(bar(T + 300, 100.5, 100.75, 100.25, 100.5, 3));
    const mid = applyLiveUpdate(s, bar(T + 420, 5, 6, 4, 5, 2), "5m");
    expect(mid[1]!.t).toBe(T + 300); // stamped with the bucket start, not the minute
  });

  it("daily: only extends the last server-defined bar, never creates buckets", () => {
    const day = [bar(T - 86400, 1, 2, 0.5, 1.5), bar(T, 10, 11, 9, 10)];
    const out = applyLiveUpdate(day, bar(T + 50_000, 10, 15, 8, 14), "1d");
    expect(out).toHaveLength(2);
    expect(out[1]).toEqual(bar(T, 10, 15, 8, 14));
    expect(applyLiveUpdate([], bar(T, 1, 1, 1, 1), "1d")).toEqual([]);
  });
});

describe("mergeBars", () => {
  it("server bars win and order is ascending", () => {
    const out = mergeBars([bar(T, 1, 1, 1, 1), bar(T + 60, 2, 2, 2, 2)], [
      bar(T + 120, 3, 3, 3, 3),
      bar(T + 60, 9, 9, 9, 9, 99),
    ]);
    expect(out.map((b) => [b.t - T, b.c])).toEqual([[0, 1], [60, 9], [120, 3]]);
  });
});

describe("price formatting", () => {
  it("uses the tick grid for decimals", () => {
    expect(tickDecimals(0.25)).toBe(2);
    expect(tickDecimals(0.1)).toBe(1);
    expect(tickDecimals(1)).toBe(0);
    expect(formatPrice(5000.5, 0.25)).toBe("5,000.50");
    expect(formatPrice(40005, 1)).toBe("40,005");
    expect(formatPrice(70.03, 0.01)).toBe("70.03");
  });
});
