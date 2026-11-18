import { expect, type Page, test } from "@playwright/test";

async function signIn(page: Page) {
  await page.goto("/login");
  await page.getByLabel("Email").fill("e2e-trader@example.com");
  await page.getByLabel("Password").fill("E2e-Correct-Horse-42!");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Market Analyzer", level: 1 })).toBeVisible();
}

const STATES = /^(LONG READY|SHORT READY|WAIT|NO TRADE|DATA UNAVAILABLE)$/;

test("entry confirmation on Advanced Charts and SMC; ask Claude; data readiness on System Health", async ({ page }) => {
  test.setTimeout(120_000);
  await signIn(page);

  await page.goto("/charts");
  const panel = page.getByTestId("entry-confirmation");
  await expect(panel).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("confirm-state")).toHaveText(STATES);
  // Simulated e2e data is labelled as such, never as LIVE.
  await expect(page.getByTestId("confirm-data-class")).not.toHaveText("LIVE");
  await page.getByTestId("confirm-toggle-all").click();
  await expect(panel.locator('[data-testid^="confirm-"][aria-expanded]')).toHaveCount(15);
  await page.getByTestId("confirm-htf_alignment").click();
  await expect(panel.getByText(/Would confirm|\{/).first()).toBeVisible();

  const ask = page.getByTestId("confirm-ask-claude");
  await expect(ask).toHaveAttribute("href", /copilot\?q=.*entry%20confirmation/);

  await page.goto("/smc");
  await expect(page.getByTestId("entry-confirmation")).toBeVisible({ timeout: 30_000 });

  await page.goto("/system");
  const dr = page.getByTestId("data-readiness");
  await expect(dr).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("dr-row-ES")).toBeVisible();
  await page.getByTestId("dr-estimate").click();
  await expect(page.getByTestId("dr-estimate-result")).toContainText("estimated cost");
});
