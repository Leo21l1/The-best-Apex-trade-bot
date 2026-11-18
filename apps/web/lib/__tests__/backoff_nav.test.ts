import { describe, expect, it } from "vitest";
import { backoffDelay } from "../backoff";
import { filterNav, NAV, navByKey } from "../nav";

describe("backoffDelay", () => {
  it("grows exponentially and is capped", () => {
    expect(backoffDelay(0, 500, 15000, () => 0)).toBe(250);
    expect(backoffDelay(0, 500, 15000, () => 1)).toBe(500);
    expect(backoffDelay(3, 500, 15000, () => 1)).toBe(4000);
    expect(backoffDelay(50, 500, 15000, () => 1)).toBe(15000);
  });
});

describe("navigation", () => {
  it("exposes the dashboard pages with unique routes and shortcuts", () => {
    expect(NAV).toHaveLength(19);
    expect(new Set(NAV.map((n) => n.href)).size).toBe(19);
    expect(new Set(NAV.map((n) => n.key)).size).toBe(19);
    expect(navByKey("y")?.href).toBe("/system");
  });
  it("filters by label and summary", () => {
    expect(filterNav("risk").map((n) => n.href)).toContain("/risk");
    expect(filterNav("drawdown").map((n) => n.href)).toContain("/account");
    expect(filterNav("zzzz")).toHaveLength(0);
  });
});
