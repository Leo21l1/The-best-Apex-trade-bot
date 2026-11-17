import { describe, expect, it } from "vitest";
import { sessionState } from "../session";

describe("sessionState", () => {
  it.each([
    ["2026-11-11T15:00:00Z", true, "Regular session"],   // Wed 10:00 ET
    ["2026-11-11T22:30:00Z", false, "Market closed"],    // Wed 17:30 ET break
    ["2026-11-11T23:30:00Z", true, "Overnight session"], // Wed 18:30 ET
    ["2026-11-14T15:00:00Z", false, "Market closed"],    // Saturday
    ["2026-11-15T23:05:00Z", true, "Overnight session"], // Sunday 18:05 ET
    ["2026-11-13T22:05:00Z", false, "Market closed"],    // Friday 17:05 ET
    ["2026-07-15T14:00:00Z", true, "Regular session"],   // EDT 10:00
  ])("%s", (iso, open, label) => {
    expect(sessionState(new Date(iso))).toEqual({ open, label });
  });
});
