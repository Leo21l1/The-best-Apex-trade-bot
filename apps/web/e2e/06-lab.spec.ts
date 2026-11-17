import { expect, type Page, test } from "@playwright/test";

async function signIn(page: Page) {
  await page.goto("/login");
  await page.getByLabel("Email").fill("e2e-trader@example.com");
  await page.getByLabel("Password").fill("E2e-Correct-Horse-42!");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();
}

const TRADES = [
  "symbol,side,qty,entry_time,exit_time,entry_price,exit_price,fees",
  "ES,long,1,2026-11-10 10:00,2026-11-10 10:30,5000,5004,5.00",
  "ES,long,1,2026-11-10 11:00,2026-11-10 11:20,5004,5000,5.00",
  "MNQ,short,3,2026-11-11 10:00,2026-11-11 10:10,18000,17990,3.60",
].join("\n");

test.describe.serial("backtesting, paper trading, journal, performance", () => {
  test("a backtest runs through the worker queue and reports labelled results", async ({ page }) => {
    test.setTimeout(120_000);
    await signIn(page);
    await page.goto("/backtesting");
    await page.getByLabel("Strategy").selectOption("vwap_trend");
    await page.getByRole("button", { name: "Run backtest" }).click();
    const result = page.getByTestId("backtest-result");
    await expect(result).toBeVisible({ timeout: 90_000 });
    await expect(result.getByText("SIMULATED DATA", { exact: true })).toBeVisible();
    await expect(page.getByTestId("validation-checks")).toContainText("simulated data can never validate");
    await expect(result.getByText("Not validated")).toBeVisible();
    await expect(page.getByTestId("backtest-runs")).toContainText("vwap_trend ES");
  });

  test("paper trading accepts, rejects and cancels simulated orders", async ({ page }) => {
    await signIn(page);
    await page.goto("/paper");
    await expect(page.getByText("Isolated simulator")).toBeVisible();
    await expect(page.getByTestId("paper-summary")).toContainText("$50,000.00");
    // Over the 6-contract limit: rejected (or market closed, also a rejection).
    await page.getByLabel("Paper symbol").selectOption("ES");
    await page.getByLabel("Paper quantity").fill("7");
    await page.getByRole("button", { name: "Submit simulated order" }).click();
    await expect(page.getByTestId("paper-msg")).toContainText(/Rejected: (would exceed the 6-contract limit|market is closed)/);
    const closed = (await page.getByTestId("paper-msg").textContent())?.includes("market is closed");
    test.skip(!!closed, "Futures market is closed; the simulator correctly rejects every order");
    // A far-away limit order rests, then is cancelled.
    await page.getByLabel("Paper quantity").fill("1");
    await page.getByLabel("Paper order type").selectOption("limit");
    await page.getByLabel("Paper price").fill("100");
    await page.getByRole("button", { name: "Submit simulated order" }).click();
    await expect(page.getByTestId("paper-msg")).toContainText("working");
    await page.getByTestId("paper-working").getByRole("button", { name: "Cancel" }).click();
    await expect(page.getByTestId("paper-working")).toHaveCount(0);
  });

  test("journal import, tagging and performance analysis", async ({ page }) => {
    await signIn(page);
    await page.goto("/journal");
    await page.getByLabel("Trades CSV").fill(TRADES);
    await page.getByRole("button", { name: "Preview" }).click();
    await expect(page.getByTestId("journal-import-result")).toContainText("3 trade(s) found (generic)");
    await page.getByRole("button", { name: /Import 3 trades/ }).click();
    await expect(page.getByTestId("journal-import-result")).toContainText("Imported 3");
    // 4pts x $50 - 5 = 195; -4pts x $50 - 5 = -205; 10pts x $2 x 3 - 3.60 = 56.40 -> 46.40
    await expect(page.getByTestId("journal-stats")).toContainText("+$46.40");
    await page.getByTestId("journal-table").locator("tbody tr").filter({ hasText: "-$205.00" }).click();
    await page.getByText("moved stop", { exact: true }).click();
    await page.getByLabel("Followed plan").selectOption("false");
    await page.getByRole("button", { name: "Save" }).click();
    await expect(page.getByTestId("journal-table")).toContainText("moved_stop");

    await page.goto("/performance");
    await page.getByLabel("Window").selectOption("3650");
    await expect(page.getByTestId("performance")).toContainText("+$46.40");
    await expect(page.getByTestId("mistakes")).toContainText("moved stop");
    await expect(page.getByTestId("mistakes")).toContainText("not enough trades in both groups");
  });
});
