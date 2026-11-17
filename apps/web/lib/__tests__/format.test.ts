import { describe, expect, it } from "vitest";
import { ageSeconds, formatAge, formatEt, formatUsd } from "../format";

describe("formatEt", () => {
  it("renders New York time across DST", () => {
    // 14:30 UTC in January = 09:30 EST; in July = 10:30 EDT.
    expect(formatEt("2026-01-15T14:30:00Z")).toBe("09:30:00 ET");
    expect(formatEt("2026-07-15T14:30:00Z")).toBe("10:30:00 ET");
  });
  it("handles invalid input", () => {
    expect(formatEt("not a date")).toBe("—");
  });
});

describe("age", () => {
  it("never negative and handles invalid", () => {
    const now = Date.parse("2026-01-01T00:00:10Z");
    expect(ageSeconds("2026-01-01T00:00:00Z", now)).toBe(10);
    expect(ageSeconds("2026-01-01T00:01:00Z", now)).toBe(0);
    expect(ageSeconds("garbage", now)).toBe(Number.POSITIVE_INFINITY);
  });
  it("formats", () => {
    expect(formatAge(5)).toBe("5s ago");
    expect(formatAge(125)).toBe("2m ago");
    expect(formatAge(7300)).toBe("2h ago");
    expect(formatAge(Number.POSITIVE_INFINITY)).toBe("unknown");
  });
});

describe("formatUsd", () => {
  it("formats signed P&L", () => {
    expect(formatUsd(1234.5)).toBe("$1,234.50");
    expect(formatUsd(-50)).toBe("-$50.00");
    expect(formatUsd(50, true)).toBe("+$50.00");
    expect(formatUsd(0, true)).toBe("$0.00");
  });
});
