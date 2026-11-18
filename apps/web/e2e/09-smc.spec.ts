import { expect, type Page, test } from "@playwright/test";

async function signIn(page: Page) {
  await page.goto("/login");
  await page.getByLabel("Email").fill("e2e-trader@example.com");
  await page.getByLabel("Password").fill("E2e-Correct-Horse-42!");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Market Analyzer", level: 1 })).toBeVisible();
}

test("smart money analysis: overlays, inspector, liquidity map, confluence", async ({ page }) => {
  test.setTimeout(120_000);
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  await signIn(page);
  await page.goto("/smc");
  await expect(page.getByTestId("smc-count")).toContainText("patterns", { timeout: 60_000 });
  await expect(page.getByTestId("candle-chart")).toBeVisible();
  for (const name of ["Sweeps", "Order blocks", "FVG", "BOS / CHoCH"]) {
    await expect(page.getByRole("button", { name, exact: true })).toHaveAttribute("aria-pressed", "true");
  }
  // Toggle a family off and on again.
  await page.getByRole("button", { name: "FVG", exact: true }).click();
  await expect(page.getByRole("button", { name: "FVG", exact: true })).toHaveAttribute("aria-pressed", "false");
  await page.getByRole("button", { name: "FVG", exact: true }).click();

  // Inspect a structure pattern from the Smart Money Analysis list.
  const list = page.getByTestId("smc-patterns");
  await expect(list).toBeVisible();
  await list.getByRole("button").first().click();
  const insp = page.getByTestId("pattern-inspector");
  await expect(insp).toContainText("Why it was detected");
  await expect(insp).toContainText("Data source");
  await expect(insp).toContainText("SIMULATED");
  await page.getByRole("button", { name: "Close inspector" }).click();

  // Liquidity map: click a level to highlight it.
  const lvl = page.getByTestId("liquidity-level").first();
  await expect(page.getByTestId("liquidity-map")).toContainText("not verified resting liquidity");
  await lvl.click();
  await expect(lvl).toHaveClass(/text-accent/);

  // Confluence: one of the four outcomes, research-only label, and simulated data never ranked.
  await expect(page.getByTestId("confluence-outcome")).toHaveText(/BUY BIAS|SHORT BIAS|WAIT|NO TRADE/);
  await expect(page.getByTestId("confluence")).toContainText("RESEARCH ONLY");

  // Switch instrument and timeframe.
  await page.getByLabel("SMC instrument").selectOption("NQ");
  await page.getByRole("group", { name: "SMC timeframe" }).getByRole("button", { name: "15m" }).click();
  await expect(page.getByTestId("smc-count")).toContainText("patterns", { timeout: 60_000 });

  // Collapsing a panel is remembered across reloads.
  await page.getByTestId("panel-smc-liquidity").getByRole("button", { name: /Liquidity map/ }).click();
  await page.reload();
  await expect(page.getByTestId("panel-smc-liquidity").getByRole("button", { name: /Liquidity map/ })).toHaveAttribute("aria-expanded", "false");
  await page.getByTestId("panel-smc-liquidity").getByRole("button", { name: /Liquidity map/ }).click();
  expect(errors).toEqual([]);
});

test("SMC scan, radar heatmap sorting, gauges, calendars, ticker, integrations", async ({ page }) => {
  test.setTimeout(120_000);
  await signIn(page);
  await page.goto("/opportunities");
  await expect(page.getByTestId("smc-scan")).toContainText("Ranked (0)", { timeout: 60_000 });
  await expect(page.getByTestId("smc-scan")).toContainText("not eligible for ranking");

  await page.goto("/radar");
  await expect(page.getByTestId("radar-table")).toBeVisible({ timeout: 30_000 });
  await page.getByTestId("sort-align").click();
  await expect(page.getByTestId("sort-align").locator("..")).toHaveAttribute("aria-sort", /descending|ascending/);

  await page.goto("/performance");
  await page.getByLabel("Window").selectOption("3650");
  await expect(page.getByTestId("panel-perf-equity")).toBeVisible();
  await expect(page.getByTestId("pnl-calendar")).toBeVisible();

  await page.goto("/journal");
  await expect(page.getByTestId("pnl-calendar")).toBeVisible();

  await expect(page.getByTestId("news-ticker")).toBeVisible();

  await page.goto("/settings");
  await page.getByTestId("integration-tests").getByRole("listitem").filter({ hasText: "Databento" }).getByRole("button", { name: "Test" }).click();
  await expect(page.getByTestId("test-databento")).toContainText("NOT CONFIGURED");

  const live = await (await page.request.get("/api/execution/live-status")).json();
  expect(live.live_trading_enabled).toBe(false);
});
