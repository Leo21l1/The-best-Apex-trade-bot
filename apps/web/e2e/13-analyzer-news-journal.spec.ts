import { expect, test } from "@playwright/test";

test.beforeEach(async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("Email").fill("e2e-trader@example.com");
  await page.getByLabel("Password").fill("E2e-Correct-Horse-42!");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Market Analyzer", level: 1 })).toBeVisible();
});

test("market analyzer: three cards, MNQ honestly unavailable, focused analysis sections", async ({ page }) => {
  const cards = page.getByTestId("instrument-cards");
  await expect(cards.getByRole("button")).toHaveCount(3, { timeout: 30_000 });
  await expect(page.getByTestId("card-ES").getByTestId("data-label")).toHaveText("SIMULATED", { timeout: 30_000 });
  await expect(page.getByTestId("card-ES").getByTestId("price")).toHaveText(/^\d{1,3}(,\d{3})*\.\d{2}$/);
  // The e2e stack does not ingest MNQ: it must say so, not borrow NQ prices.
  await expect(page.getByTestId("card-MNQ").getByTestId("data-label")).toHaveText("UNAVAILABLE");
  await expect(page.getByTestId("card-MNQ").getByTestId("price")).toHaveText("—");
  await expect(page.getByTestId("card-MNQ").getByTestId("bias")).toHaveText("WAIT / INSUFFICIENT DATA");
  await expect(page.getByTestId("card-MNQ").getByTestId("card-state")).toHaveText("WAIT");
  await expect(page.getByTestId("card-ES").getByTestId("card-state")).toHaveText(/^(BUY SETUP|SELL SETUP|DEVELOPING BUY|DEVELOPING SELL|WAIT|INVALIDATED|EXPIRED|MISSED)$/);
  await expect(page.getByTestId("card-ES").getByTestId("card-confluence")).toHaveText(/^[0-5]\/5$/);

  await page.getByTestId("card-NQ").click();
  const detail = page.getByTestId("analysis-detail");
  // Conclusion first: technical state, confluence count, the five confirmations.
  await expect(page.getByTestId("signal-state")).toHaveText(/^(TECHNICAL (BUY|SELL) SETUP CONFIRMED|DEVELOPING (BUY|SELL)|WAIT|INVALIDATED|EXPIRED|MISSED)$/, { timeout: 30_000 });
  await expect(page.getByTestId("signal-confluence")).toHaveText(/^Confluence [0-5]\/5$/);
  await expect(detail.getByText("1 · Market bias")).toBeVisible();
  await expect(page.getByTestId("bias-sentence")).toContainText(/bias|structure/i);
  await expect(detail.getByText("4 · Confluence", { exact: false })).toBeVisible();
  await expect(page.getByTestId("five-factors").locator("li")).toHaveCount(5);
  await expect(detail.getByText("5 · Market structure & key levels")).toBeVisible();
  await expect(detail.getByText("6 · Chart")).toBeVisible();
  await expect(page.getByTestId("overlay-toggles").getByRole("checkbox")).toHaveCount(7);
  await page.getByTestId("advanced").locator("summary").click();
  await expect(page.getByText("Smart Money setups")).toBeVisible();
  await expect(page.getByText(/not a probability/).first()).toBeVisible();
  await expect(page.getByTestId("forward-stats")).toContainText("4/5 balanced (live threshold)");
  await page.screenshot({ path: "test-results/visual/desktop-analyzer.png", fullPage: true });
  // Analysis only: no order buttons anywhere on the analyzer.
  await expect(page.getByRole("button", { name: /^(buy|sell|place order|submit order)/i })).toHaveCount(0);
});

test("news tab: status, freshness and honest calendar note", async ({ page }) => {
  await page.getByRole("navigation", { name: "Primary" }).getByRole("link", { name: "News" }).click();
  await expect(page.getByRole("heading", { name: "News", level: 1 })).toBeVisible();
  // The e2e stack disables ForexFactory downloads; an imported ICS calendar (spec 05) may be present.
  await expect(page.getByTestId("restriction")).toBeVisible();
  await expect(page.getByTestId("news-status")).toHaveText(
    /^(HIGH IMPACT UPCOMING|NEWS WINDOW ACTIVE|POST-NEWS VOLATILITY|NO MAJOR SCHEDULED EVENT NEARBY|NEWS DATA STALE \/ UNAVAILABLE)$/,
  );
  await expect(page.getByTestId("freshness")).toHaveText(/^(Up to date|STALE \/ UNAVAILABLE)$/);
  await expect(page.getByText("Last successful download:")).toBeVisible();
  await expect(page.getByText(/not a breaking-news feed/)).toBeVisible();
  await page.screenshot({ path: "test-results/visual/desktop-news.png", fullPage: true });
});

test("journal: record a scaled trade with partial exits and see net P&L", async ({ page }) => {
  await page.getByRole("navigation", { name: "Primary" }).getByRole("link", { name: "Trading Journal" }).click();
  const form = page.getByTestId("record-trade");
  await form.getByLabel("Symbol").selectOption("NQ");
  await form.getByRole("button", { name: "+ exit" }).click();
  await form.getByLabel("Fill 1 time").fill("2026-11-17T10:00");
  await form.getByLabel("Fill 1 quantity").fill("2");
  await form.getByLabel("Fill 1 price").fill("21000");
  await form.getByLabel("Fill 2 time").fill("2026-11-17T10:10");
  await form.getByLabel("Fill 2 quantity").fill("1");
  await form.getByLabel("Fill 2 price").fill("21010");
  await form.getByLabel("Fill 3 time").fill("2026-11-17T10:20");
  await form.getByLabel("Fill 3 quantity").fill("1");
  await form.getByLabel("Fill 3 price").fill("20995");
  await form.getByLabel("Fees").fill("8.40");
  await form.getByLabel("Stop-loss").fill("20990");
  // (10 - 5) points x $20 = $100 gross, $91.60 net.
  await expect(page.getByTestId("pnl-preview")).toContainText("Gross +$100.00");
  await expect(page.getByTestId("pnl-preview")).toContainText("net +$91.60");
  await form.getByRole("button", { name: "Save trade" }).click();
  await expect(form.getByText("Trade recorded (MANUALLY ENTERED).")).toBeVisible();
  await expect(page.getByTestId("journal-table")).toContainText("NQ");
  await expect(page.getByTestId("journal-stats")).toContainText("Average R");
  await expect(page.getByTestId("journal-performance")).toContainText("+$");
  await page.screenshot({ path: "test-results/visual/desktop-journal.png", fullPage: true });
});
