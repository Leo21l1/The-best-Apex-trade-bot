import { expect, type Page, test } from "@playwright/test";

async function signIn(page: Page) {
  await page.goto("/login");
  await page.getByLabel("Email").fill("e2e-trader@example.com");
  await page.getByLabel("Password").fill("E2e-Correct-Horse-42!");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Market Analyzer", level: 1 })).toBeVisible();
}

test.describe.serial("account health and risk guardian", () => {
  test("risk is unknown before any balance is entered", async ({ page }) => {
    await signIn(page);
    await page.goto("/account");
    await expect(page.getByText("Account risk is unknown")).toBeVisible();
    await expect(page.getByText("Apex rule profile is UNVERIFIED")).toBeVisible();
    await page.goto("/risk");
    await page.getByLabel("Entry").fill("5000");
    await page.getByLabel("Stop").fill("4996");
    await page.getByRole("button", { name: "Check trade" }).click();
    await expect(page.getByTestId("risk-verdict")).toContainText("Cannot confirm");
  });

  test("CSV import and manual snapshot drive the EOD drawdown figures", async ({ page }) => {
    await signIn(page);
    await page.goto("/account");
    await page.getByLabel("CSV content").fill("Date,End of Day Balance\n2026-11-09,50400\n2026-11-10,51200\n");
    await page.getByRole("button", { name: "Preview" }).click();
    await expect(page.getByTestId("csv-preview")).toContainText("2 row(s)");
    await page.getByRole("button", { name: /Import 2 rows/ }).click();
    await expect(page.getByTestId("csv-preview")).toContainText("Imported 2");

    await page.getByLabel("Balance (closed)").fill("50700");
    await page.getByLabel("Unrealised P&L").fill("-150");
    await page.getByRole("button", { name: "Add position" }).click();
    await page.getByLabel("Quantity (negative = short)").fill("1");
    await page.getByLabel("Average price").fill("5000");
    await page.getByLabel("Stop price").fill("4995");
    await page.getByRole("button", { name: "Save snapshot" }).click();
    await expect(page.getByText("Snapshot saved.")).toBeVisible();

    // Threshold = highest EOD 51,200 - 2,000 = 49,200; equity 50,550 -> room 1,350.
    const stats = page.getByTestId("account-stats");
    await expect(stats).toContainText("$49,200.00");
    await expect(page.getByTestId("drawdown-room")).toHaveText("$1,350.00");
    // Gauges: EOD drawdown used = 1 - 1,350 / 2,000.
    await expect(page.getByTestId("gauge-drawdown")).toContainText("$1,350.00 above threshold $49,200.00");
    await expect(page.getByTestId("gauge-drawdown")).toHaveAttribute("aria-label", /EOD drawdown used: 3[23]%/);
    await expect(page.getByTestId("snapshot-table").locator("tbody tr")).toHaveCount(3);
  });

  test("risk guardian reports the six outputs and flags violations", async ({ page }) => {
    await signIn(page);
    await page.goto("/risk");
    await page.getByLabel("Entry").fill("5000");
    await page.getByLabel("Stop").fill("4996");
    await page.getByLabel("Target").fill("5008");
    await page.getByLabel("Contracts").fill("1");
    await page.getByRole("button", { name: "Check trade" }).click();
    const out = page.getByTestId("risk-outputs");
    await expect(out).toContainText("$200.00"); // 16 ticks x $12.50
    await expect(out).toContainText("$230.00"); // + $30 assumed round-trip cost
    await expect(out).toContainText("2.00 R");
    await expect(page.getByTestId("risk-verdict")).toContainText("UNVERIFIED");

    // Down $650 today from the $51,200 EOD start: $350 daily allowance left, so 2 contracts ($460) violate.
    await page.getByLabel("Contracts").fill("2");
    await page.getByRole("button", { name: "Check trade" }).click();
    await expect(page.getByTestId("risk-verdict")).toContainText("Violates");
    await expect(page.getByText(/exceeds the remaining daily loss allowance \$350/)).toBeVisible();

    // Exceed the 6-contract limit (1 ES already open).
    await page.getByLabel("Contracts").fill("6");
    await page.getByRole("button", { name: "Check trade" }).click();
    await expect(page.getByTestId("risk-verdict")).toContainText("Violates");
    await expect(page.getByText(/Contract limit/)).toBeVisible();

    // Invalid stop is rejected with a reason.
    await page.getByLabel("Stop").fill("5004");
    await page.getByRole("button", { name: "Check trade" }).click();
    await expect(page.getByRole("alert").filter({ hasText: "stop must be below entry" })).toBeVisible();
  });

  test("admin verifies a rule against an official source", async ({ page }) => {
    await signIn(page);
    await page.goto("/account");
    const row = page.getByTestId("rule-max_drawdown");
    await row.getByRole("button", { name: "Edit / verify" }).click();
    await page.getByLabel(/Official source URL/).fill("https://example.com/rules");
    await page.getByLabel("Version / page date").fill("checked 2026-11-17");
    await page.getByLabel(/I checked the current value/).check();
    await page.getByRole("button", { name: "Mark verified" }).click();
    await expect(page.getByText(/official Apex Trader Funding page/)).toBeVisible();
    await page.getByLabel(/Official source URL/).fill(
      "https://apextraderfunding.com/help-center/eod-trailing-drawdown-accounts/eod-evaluations/");
    await page.getByRole("button", { name: "Mark verified" }).click();
    await expect(row).toContainText("Verified");
    await expect(row.getByRole("link", { name: /checked 2026-11-17/ })).toBeVisible();
    // Still 14 other rules unverified, so the profile as a whole stays unverified.
    await expect(page.getByText("Apex rule profile is UNVERIFIED")).toBeVisible();
  });
});
