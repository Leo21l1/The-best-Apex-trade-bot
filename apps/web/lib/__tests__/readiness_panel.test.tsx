import type { Readiness } from "@apex/shared-types";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

const DATA: Readiness = {
  session: { ny_time: "2026-11-17 12:45:00 EST", trading_date: "2026-11-17", window: { key: "lunch", label: "Typical midday / lunch window", start: "11:30", end: "13:30" },
    next_transition: { label: "Afternoon session", at: "", at_et: "13:30", in_minutes: 45 }, market: { open: true, reason: "Exchange session open", early_close: false, calendar: "CME_Equity" } },
  data: { status: "LIVE", last_bar: "2026-11-17T17:44:00+00:00" },
  news: { status: "clear", summary: "No high/medium event" },
  risk: { known: true, risk_budget: 800, rules_verified: false },
  setup: null,
  checklist: {
    symbol: "MNQ", as_of: "2026-11-17T17:45:00+00:00", ny_time: "2026-11-17 12:45 EST", style: "intraday",
    window: { key: "lunch", label: "Typical midday / lunch window", start: "11:30", end: "13:30" },
    market: { open: true, reason: "Exchange session open", early_close: false, calendar: "CME_Equity" },
    items: [
      { key: "kill_zone", name: "Kill Zone / Afternoon", status: "FAIL", reason: "outside" },
      { key: "volume", name: "Above-Average Volume", status: "UNKNOWN", reason: "few sessions" },
      { key: "setup", name: "Setup Detected", status: "WAIT", reason: "no setup" },
      { key: "outside_lunch", name: "Outside Lunch Hours", status: "PASS", reason: "exception" },
    ],
    passed: 1, total: 4, failures: ["Kill Zone / Afternoon"], unknowns: ["Above-Average Volume"], critical: [], status: "WAIT", direction: null,
    relative_volume: { available: false, reason: "few sessions" }, relative_volatility: { available: false, reason: "n/a" },
    regime: { label: "choppy", efficiency_ratio_2h: 0.1, vwap_crosses_today: 5, definition: "" }, disclaimer: "context only",
  },
  liquidity_note: "",
};

vi.mock("@/hooks/usePolling", () => ({ usePolling: () => ({ data: DATA, error: null }) }));
vi.mock("@/lib/api", () => ({ api: () => Promise.resolve(DATA) }));

describe("ReadinessPanel", async () => {
  const { ReadinessPanel } = await import("@/features/session/ReadinessPanel");
  it("shows status, count and every condition with a text label, not colour alone", () => {
    render(<ReadinessPanel symbol="MNQ" />);
    expect(screen.getByTestId("readiness-status").textContent).toBe("WAIT");
    expect(screen.getByTestId("readiness-count").textContent).toBe("1/4 pass");
    expect(screen.getByTestId("check-kill_zone").textContent).toContain("✗ FAIL");
    expect(screen.getByTestId("check-volume").textContent).toContain("? UNKNOWN");
    expect(screen.getByTestId("check-setup").textContent).toContain("… WAIT");
    expect(screen.getByTestId("readiness-ny").textContent).toContain("12:45");
    expect(screen.getByText(/Failing: Kill Zone/)).toBeTruthy();
  });
});
