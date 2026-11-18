import type { EntryConfirmation } from "@apex/shared-types";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const BASE: EntryConfirmation = {
  symbol: "ES", contract: "ESZ6", provider: "databento", state: "LONG READY", summary: "All mandatory conditions pass for a long.",
  strategy_id: "vwap_trend", direction: "long", data_class: "LIVE", data_timestamp: "2026-11-17T15:30:00+00:00",
  as_of: "2026-11-17T15:31:00+00:00", ny_time: "2026-11-17 10:31 EST",
  conditions: [
    { key: "trigger", name: "Entry trigger", status: "PASS", reason: "vwap_trend long trigger", evidence: {}, mandatory: true },
    { key: "news", name: "News", status: "PASS", reason: "clear", evidence: {}, mandatory: true },
    { key: "history", name: "Historical performance", status: "UNKNOWN", reason: "No backtest", evidence: {}, mandatory: false },
  ],
  mandatory: ["trigger", "news"], passed: 2, total: 3, missing: [],
  levels: {
    direction: "long", entry_zone: [5014.25, 5015.75], entry: 5015, entry_with_slippage: 5015.25, trigger: "vwap_trend on the 5m bar of 10:25 ET",
    stop: 5010, t1: 5025, t2: 5030, extended_target: null, extended_basis: null, stop_points: 5, stop_ticks: 20,
    loss_per_contract: 280, reward_per_contract_t1: 470, reward_per_contract_t2: 720, rr_t1: 1.68, rr_t2: 2.57, point_value: 50,
    tick_size: 0.25, costs_per_contract: 30, costs_if_target: 17.5, gross_rr_t1: 2, gross_rr_t2: 3, min_net_rr: 1.5, invalidation_price: 5010, expires_at: "2026-11-17T15:45:00+00:00", basis: "Signal levels.",
  },
  max_size: 3, apex_compliance: "NOT CONFIRMED - rule profile unverified", news_status: "CLEAR",
  next_eligible_window: null, regime: { labels: ["bullish trend"] }, expires_at: "2026-11-17T15:45:00+00:00",
  disclaimer: "Analysis only: PROJECT APEX never submits, modifies or cancels orders.",
};
let DATA: EntryConfirmation = BASE;
const post = vi.fn(() => Promise.resolve({ id: 7 }));

vi.mock("@/hooks/usePolling", () => ({ usePolling: () => ({ data: DATA, error: null }) }));
vi.mock("@/hooks/useSession", () => ({ useSession: () => ({ user: { role: "admin" } }) }));
vi.mock("@/lib/api", () => ({ api: (path: string, init?: { method?: string }) => (init?.method === "POST" ? post() : Promise.resolve(DATA)) }));
vi.mock("next/link", () => ({ default: ({ href, children, ...p }: { href: string; children: React.ReactNode }) => <a href={href} {...p}>{children}</a> }));

describe("EntryConfirmationPanel", async () => {
  const { EntryConfirmationPanel } = await import("@/features/session/EntryConfirmation");
  beforeEach(() => { DATA = BASE; post.mockClear(); });
  afterEach(cleanup);

  it("shows the state, tick-formatted levels, max size and compliance wording", () => {
    render(<EntryConfirmationPanel symbol="ES" />);
    expect(screen.getByTestId("confirm-state").textContent).toBe("LONG READY");
    expect(screen.getByTestId("confirm-stop").textContent).toBe("5010.00");
    expect(screen.getByTestId("confirm-max-size").textContent).toBe("3");
    expect(screen.getByText(/rule profile unverified/)).toBeTruthy();
    expect(screen.getByTestId("confirm-data-class").textContent).toBe("LIVE");
    expect(screen.getByTestId("confirm-rr-t1").textContent).toContain("1.68");
    expect(screen.getByTestId("confirm-rr-t1").textContent).toContain("gross 2");
    expect(screen.getByText("Net R:R to T1 (min 1.5)")).toBeTruthy();
    expect(screen.getByTestId("confirm-rr-t2").textContent).toContain("2.57");
  });

  it("saves a plan without sending an order and links to the Terminal", async () => {
    render(<EntryConfirmationPanel symbol="ES" />);
    fireEvent.click(screen.getByTestId("confirm-save-plan"));
    expect(await screen.findByTestId("confirm-saved")).toBeTruthy();
    expect(screen.getByTestId("confirm-saved").textContent).toContain("no order sent");
    expect(post).toHaveBeenCalledTimes(1);
    const ask = screen.getByTestId("confirm-ask-claude").getAttribute("href") ?? "";
    expect(decodeURIComponent(ask)).toContain("state LONG READY");
  });

  it("explains a WAIT, flags unknown news and disables saving without levels", () => {
    DATA = {
      ...BASE, state: "WAIT", levels: null, max_size: null, news_status: "NEWS STATUS UNKNOWN", summary: "2 mandatory condition(s) not yet met",
      missing: [{ key: "trigger", name: "Entry trigger", status: "WAIT", reason: "No signal", would_confirm: "a strategy signal on a completed 5m bar" }],
      next_eligible_window: { start: "", start_et: "13:30", end_et: "15:30", date: "2026-11-17", in_minutes: 85, now: false },
    };
    render(<EntryConfirmationPanel symbol="ES" />);
    expect(screen.getByTestId("confirm-state").textContent).toBe("WAIT");
    expect(screen.getByTestId("confirm-news-unknown")).toBeTruthy();
    expect(screen.getByTestId("confirm-missing").textContent).toContain("would confirm: a strategy signal");
    expect(screen.getByTestId("confirm-next-window").textContent).toContain("13:30");
    expect((screen.getByTestId("confirm-save-plan") as HTMLButtonElement).disabled).toBe(true);
    expect(screen.queryByTestId("confirm-levels")).toBeNull();
  });

  it("hides optional passing conditions until expanded", () => {
    render(<EntryConfirmationPanel symbol="ES" />);
    expect(screen.queryByTestId("confirm-history")).toBeNull();
    fireEvent.click(screen.getByTestId("confirm-toggle-all"));
    expect(screen.getByTestId("confirm-history").textContent).toContain("optional");
  });
});
