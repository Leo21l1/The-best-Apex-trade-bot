import { describe, expect, it } from "vitest";
import { money, progress } from "../money";

describe("money", () => {
  it("never renders unknown as zero", () => {
    expect(money(null)).toBe("Unknown");
    expect(money(undefined)).toBe("Unknown");
    expect(money(Number.NaN)).toBe("Unknown");
    expect(money(0)).toBe("$0.00");
  });
  it("formats signs", () => {
    expect(money(-450)).toBe("-$450.00");
    expect(money(1550, true)).toBe("+$1,550.00");
    expect(money(1550)).toBe("$1,550.00");
  });
  it("progress clamps", () => {
    expect(progress(49000, 48000, 53000)).toBeCloseTo(0.2);
    expect(progress(47000, 48000, 53000)).toBe(0);
    expect(progress(60000, 48000, 53000)).toBe(1);
    expect(progress(5, 5, 5)).toBe(0);
  });
});
