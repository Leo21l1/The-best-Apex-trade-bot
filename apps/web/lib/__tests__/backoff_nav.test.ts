import { describe, expect, it } from "vitest";
import { backoffDelay } from "../backoff";
import { ADVANCED, filterNav, NAV, navByKey, TABS, VISIBLE } from "../nav";

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
    expect(NAV).toHaveLength(21);
    expect(new Set(NAV.map((n) => n.href)).size).toBe(21);
    expect(new Set(NAV.map((n) => n.key)).size).toBe(21);
    expect(navByKey("y")?.href).toBe("/system");
  });
  it("shows only the three primary tabs plus settings/health in normal navigation", () => {
    expect([...TABS]).toEqual(["/", "/news", "/journal"]);
    expect(VISIBLE.map((n) => n.href).sort()).toEqual(["/", "/journal", "/news", "/settings", "/system"]);
    expect(navByKey("m")).toBeUndefined(); // paper simulator is not reachable from the keyboard
    expect(ADVANCED).not.toContain("/paper");
    expect(ADVANCED).not.toContain("/terminal");
  });
  it("filters visible pages by label and summary", () => {
    expect(filterNav("calendar").map((n) => n.href)).toContain("/news");
    expect(filterNav("journal").map((n) => n.href)).toContain("/journal");
    expect(filterNav("risk guardian")).toHaveLength(0);
    expect(filterNav("zzzz")).toHaveLength(0);
  });
});
