import { expect, type Page, test } from "@playwright/test";

async function signIn(page: Page) {
  await page.goto("/login");
  await page.getByLabel("Email").fill("e2e-trader@example.com");
  await page.getByLabel("Password").fill("E2e-Correct-Horse-42!");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();
}

const ORDERS = `orderId,Account,Order ID,B/S,Contract,Fill Time,Status,Filled Qty,Avg Fill Price
1,APEX-1,9001, Buy,M2KZ6,11/12/2026 10:00:00, Filled,2,2400.00
2,APEX-1,9002, Sell,M2KZ6,11/12/2026 10:05:00, Filled,2,2403.00
3,APEX-1,9003, Sell,M2KZ6,, Canceled,0,
`;
const CASH = `Date,Cash Change Type,Delta,Total Amount
11/12/2026,Trade Paired,30.00,50030.00
11/12/2026,Commission,-2.40,50027.60
`;

test("broker statement import: policy, preview, duplicates, apply, reconciliation", async ({ page }) => {
  await signIn(page);
  await page.goto("/journal");
  await expect(page.getByText("Automatic account sync is not enabled")).toBeVisible();
  await page.getByTestId("policy-toggle").click();
  await expect(page.getByTestId("broker-policy")).toContainText("evaluation accounts are not eligible");

  await page.getByLabel("Broker CSV files").setInputFiles([
    { name: "orders.csv", mimeType: "text/csv", buffer: Buffer.from(ORDERS) },
    { name: "cash.csv", mimeType: "text/csv", buffer: Buffer.from(CASH) },
  ]);
  await page.getByRole("button", { name: "Check files" }).click();
  const result = page.getByTestId("broker-result");
  await expect(result).toContainText("Preview: 1 new trade(s)");
  await expect(result).toContainText("Orders / fills (paired FIFO)");
  await expect(result).toContainText("1 unfilled orders skipped");
  await expect(page.getByTestId("reconciliation")).toContainText("30.00 ✓");

  await page.getByTestId("broker-apply").click();
  await expect(result).toContainText("Imported 1 trade(s)");
  await expect(page.getByTestId("journal-table")).toContainText("M2K");

  // Importing the same files again finds only duplicates.
  await page.getByRole("button", { name: "Check files" }).click();
  await expect(result).toContainText("0 new trade(s), 1 duplicate(s)");
});
