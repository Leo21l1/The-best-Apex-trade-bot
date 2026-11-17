import { expect, type Page, test } from "@playwright/test";

const EMAIL = "e2e-trader@example.com";
const PASSWORD = "E2e-Correct-Horse-42!";
const PRICE = /^\d{1,3}(,\d{3})*(\.\d+)?$/;

async function signIn(page: Page) {
  await page.goto("/login");
  await page.getByLabel("Email").fill(EMAIL);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();
}

test.describe("advanced charts", () => {
  test("renders labelled candles and switches timeframe and instrument", async ({ page }) => {
    await signIn(page);
    await page.keyboard.press("g");
    await page.keyboard.press("c");
    await expect(page.getByRole("heading", { name: "Advanced Charts" })).toBeVisible();

    // Provenance is explicit: provider, data class, timestamp.
    await expect(page.getByText("SIMULATED DATA")).toBeVisible({ timeout: 30_000 });
    await expect(page.getByTestId("chart-provider")).toContainText("simulated");
    await expect(page.getByTestId("chart-last-time")).toContainText("ET");
    await expect(page.getByTestId("candle-chart").locator("canvas").first()).toBeVisible();
    const es = page.getByTestId("chart-last-price");
    await expect(es).toHaveText(PRICE);
    const esPrice = await es.textContent();

    // Timeframes: every button loads bars.
    for (const tf of ["1m", "1h", "1d", "5m"]) {
      await page.getByRole("button", { name: tf, exact: true }).click();
      await expect(page.getByRole("button", { name: tf, exact: true })).toHaveAttribute(
        "aria-pressed", "true");
      await expect(page.getByTestId("candle-chart").locator("canvas").first()).toBeVisible();
      await expect(es).toHaveText(PRICE);
    }

    // Instrument switch shows a different market and its own tick precision (CL: 2 decimals).
    await page.getByLabel("Instrument").selectOption("CL");
    await expect(es).toHaveText(/^\d{2,3}\.\d{2}$/);
    expect(await es.textContent()).not.toBe(esPrice);

    // Data quality panel reports complete simulated history.
    await expect(page.getByTestId("gap-missing")).toHaveText("0");
  });

  test("receives live bar updates over the WebSocket while the market is open", async ({ page }) => {
    await signIn(page);
    const status = await (await page.request.get("/api/market/status")).json();
    test.skip(!status.market_open, "Futures market is closed; the feed correctly emits no bars");
    await page.goto("/charts");
    await page.getByRole("button", { name: "1m", exact: true }).click();
    await expect(page.getByText(/stream update \d{2}:\d{2}:\d{2} ET/)).toBeVisible({
      timeout: 20_000,
    });
  });
});
