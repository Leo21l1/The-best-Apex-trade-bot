import { expect, type Page, test } from "@playwright/test";

async function signIn(page: Page) {
  await page.goto("/login");
  await page.getByLabel("Email").fill("e2e-trader@example.com");
  await page.getByLabel("Password").fill("E2e-Correct-Horse-42!");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Market Analyzer", level: 1 })).toBeVisible();
}

test.describe("scanner, opportunities, strategies, alerts", () => {
  test("market radar shows every scanned symbol with explainable checks", async ({ page }) => {
    await signIn(page);
    await page.goto("/radar");
    await expect(page.getByTestId("radar-table")).toBeVisible({ timeout: 45_000 });
    for (const s of ["ES", "NQ", "CL"]) await expect(page.getByTestId(`radar-${s}`)).toBeVisible();
    await expect(page.getByText("SIMULATED DATA")).toBeVisible();

    // Filter and watchlist
    await page.getByLabel("Filter symbol").fill("nq");
    await expect(page.getByTestId("radar-ES")).toHaveCount(0);
    await page.getByLabel("Filter symbol").fill("");
    await page.getByRole("button", { name: "Watchlist CL" }).click();
    await page.getByLabel(/Watchlist only/).check();
    await expect(page.getByTestId("radar-CL")).toBeVisible();
    await expect(page.getByTestId("radar-ES")).toHaveCount(0);
    await page.reload(); // watchlist persists in the browser
    await expect(page.getByRole("button", { name: "Watchlist CL" })).toHaveAttribute("aria-pressed", "true", { timeout: 45_000 });

    // Expanding a symbol lists levels and all eight strategy checks.
    await page.getByTestId("radar-CL").getByRole("button", { name: "CL", exact: true }).click();
    await expect(page.getByText("Strategy checks (latest closed 5m bar)")).toBeVisible();
    for (const id of ["orb", "vwap_trend", "sweep_reversal", "breakout_retest", "momentum_pullback", "vol_expansion", "mean_reversion", "mtf_trend"]) {
      await expect(page.getByText(id, { exact: true })).toBeVisible();
    }
  });

  test("leaderboard ranks nothing while strategies are unvalidated", async ({ page }) => {
    await signIn(page);
    await page.goto("/opportunities");
    await expect(page.getByTestId("ranked-empty")).toContainText("Zero ranked opportunities is a valid result");
    await page.getByLabel("Active only").uncheck();
    // Either candidates exist (and explain themselves) or the page says there are none.
    const table = page.getByTestId("candidates-table");
    const none = page.getByText("No candidates", { exact: true });
    await expect(table.or(none)).toBeVisible({ timeout: 20_000 });
    if (await table.isVisible()) {
      await table.locator("tbody tr").first().click();
      await expect(page.getByText("Why it is not ranked")).toBeVisible();
      await expect(page.getByText(/Strategy not validated/).first()).toBeVisible();
    }
  });

  test("strategy laboratory lists eleven unvalidated specifications", async ({ page }) => {
    await signIn(page);
    await page.goto("/strategies");
    await expect(page.getByText("No strategy is validated")).toBeVisible();
    for (const id of ["orb", "vwap_trend", "sweep_reversal", "breakout_retest", "momentum_pullback", "vol_expansion", "mean_reversion", "mtf_trend", "smc_sweep_mss", "smc_ob_retest", "smc_fvg_retest"]) {
      await expect(page.getByTestId(`strategy-${id}`)).toBeVisible();
    }
    await expect(page.getByText("Unvalidated", { exact: true })).toHaveCount(11);
  });

  test("alerts panel opens and can be cleared", async ({ page }) => {
    await signIn(page);
    await page.getByRole("button", { name: /^Alerts,/ }).click();
    await expect(page.getByRole("dialog", { name: "Alerts" })).toBeVisible();
    await page.getByRole("button", { name: "Mark all read" }).click({ force: true });
    await expect(page.getByTestId("alerts-unread")).toHaveCount(0);
  });
});
