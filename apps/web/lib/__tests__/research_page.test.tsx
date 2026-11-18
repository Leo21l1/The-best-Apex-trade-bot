import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const stats = (trades: number, wr: number, lb: number, exp: number) => ({
  trades, wins: Math.round(trades * wr), losses: trades - Math.round(trades * wr), breakeven: 0, win_rate: wr,
  win_rate_ci95: [lb, Math.min(1, wr + 0.1)], win_rate_lower_bound: lb, avg_win: 100, avg_loss: -80, expectancy: exp,
  median_pnl: exp, expectancy_ci: [exp - 20, exp + 20], avg_r: 0.1, profit_factor: 1.3, gross_profit: 1000,
  gross_loss: -800, net_pnl: 200, max_drawdown: 300, max_consecutive_losses: 4, trades_per_session: 0.5,
  profitable_sessions_pct: 0.55, avg_daily_pnl: 5, costs_pct_of_gross_profit: 0.2, sharpe_daily: 1.1, monthly: {},
  equity_curve: [],
});
const row = (sid: string, s: ReturnType<typeof stats>, evidence: string) => ({
  strategy_id: sid, config: "t1_all", config_label: "Exit at T1", evidence, unmet_requirements: ["holdout"],
  research: { all: s, long: s, short: s }, walk_forward: { all: s, long: s, short: s }, holdout: { all: s, long: s, short: s },
  all_periods: { all: s, long: s, short: s }, walk_forward_blocks: [], blocks_positive_pct: null,
  stress: { slip2: { label: "2 ticks", trades: s.trades, win_rate: 0.4, expectancy: -1, profit_factor: 0.9, net_pnl: 0, max_drawdown: 0 } },
  apex_replay: { rules: { label: "UNVERIFIED rule profile", profit_target: 3000, max_drawdown: 2000, daily_loss_limit: 1000, daily_loss_treated_as: "day stop" }, attempts: 1, resolved: 0, passes: 0, fails: 0, pass_rate: null, pass_rate_ci95: null, median_days_to_pass: null, note: "" },
  funnel: { signals: 3 }, by_regime: {}, by_session: {}, walk_forward_expectancy_ci_adjusted: null, walk_forward_ci_level: 0.995,
});
const STUDY = {
  id: 3, created_at: "2026-11-18T00:00:00Z", symbol: "ES", data_class: "SIMULATED",
  headline: { available: false, reason: "Simulated data: no findings." },
  result: {
    symbol: "ES", data_class: "SIMULATED", generated_at: "2026-11-18T00:00:00Z", comparisons: 10,
    data: { bars: 1000, usable_sessions: 25, verdict: "SUFFICIENT" }, plan: { rule: "research 10", preliminary: true, sessions: 25 },
    rollover: { excluded_sessions: {}, segments: [{ contract: "ESZ6", first: "", last: "", bars: 1000 }], discontinuities: [] },
    costs: { commission_per_side: 2.5, slippage_ticks_per_side: 1 }, definitions: { win: "net > 0" },
    rows: [row("orb", stats(10, 0.9, 0.596, 50), "Insufficient evidence"), row("vwap_trend", stats(600, 0.667, 0.628, 12), "Promising but unvalidated")],
  },
};
let empty = false;
vi.mock("@/components/shell/PageHeader", () => ({ PageHeader: () => <h1>Strategy Research</h1> }));
vi.mock("@/lib/api", () => ({
  api: (path: string) => {
    if (path.startsWith("/api/research/inventory")) return Promise.resolve([]);
    if (path.startsWith("/api/research/studies?")) return Promise.resolve(empty ? [] : [{ id: 3 }]);
    if (path.includes("/trades")) return Promise.resolve({ trades: [] });
    return Promise.resolve(STUDY);
  },
}));

describe("Research page", async () => {
  const { Research } = await import("@/features/research/Research");
  afterEach(() => { cleanup(); empty = false; });

  it("says when no study exists instead of showing placeholder rankings", async () => {
    empty = true;
    render(<Research />);
    expect(await screen.findByText(/No research study for ES yet/)).toBeTruthy();
    expect(screen.queryByTestId("research-leaderboard")).toBeNull();
  });

  it("labels simulated data, ranks by the conservative lower bound and flags small samples", async () => {
    render(<Research />);
    expect(await screen.findByText("SIMULATED DATA")).toBeTruthy();
    expect(screen.getByTestId("research-headline").textContent).toContain("No headline");
    const rows = await screen.findAllByTestId(/^research-row-/);
    expect(rows[0]!.textContent).toContain("vwap_trend"); // 400/600 outranks 9/10 on the lower bound
    expect(rows[1]!.textContent).toContain("⚠");
    fireEvent.click(rows[0]!);
    await waitFor(() => expect(screen.getByTestId("research-detail")).toBeTruthy());
    expect(screen.getByTestId("research-apex").textContent).toContain("UNVERIFIED");
  });
});
