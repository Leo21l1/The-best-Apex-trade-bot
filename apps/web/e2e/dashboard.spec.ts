import { expect, test } from "@playwright/test";

const EMAIL = "e2e-trader@example.com";
const PASSWORD = "E2e-Correct-Horse-42!";

test.describe.serial("dashboard workflows", () => {
  test("unauthenticated users are redirected to sign-in", async ({ page }) => {
    await page.goto("/system");
    await expect(page).toHaveURL(/\/login$/);
  });

  test("first-run bootstrap creates admin and lands on overview", async ({ page }) => {
    await page.goto("/login");
    await expect(page.getByRole("heading", { name: "Create administrator account" })).toBeVisible();
    await page.getByLabel("Email").fill(EMAIL);
    await page.getByLabel("Password", { exact: true }).fill(PASSWORD);
    await page.getByLabel("Confirm password").fill(PASSWORD);
    await page.getByRole("button", { name: "Create account" }).click();

    await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();
    await expect(page.getByTestId("execution-mode")).toHaveText("ANALYSIS ONLY");
    await expect(page.getByText("Apex rule profile is UNVERIFIED")).toBeVisible();
    // Honest empty states: no fabricated prices or opportunities.
    await expect(page.getByText("No licensed provider connected")).toBeVisible();
    await expect(page.getByText("No opportunities ranked")).toBeVisible();
  });

  test("sign-in, keyboard navigation, live system health, sign-out", async ({ page }) => {
    await page.goto("/login");
    await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
    await page.getByLabel("Email").fill(EMAIL);
    await page.getByLabel("Password").fill("wrong-password");
    await page.getByRole("button", { name: "Sign in" }).click();
    await expect(page.getByText("Invalid email or password")).toBeVisible();

    await page.getByLabel("Password").fill(PASSWORD);
    await page.getByRole("button", { name: "Sign in" }).click();
    await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();

    // "g y" chord -> System Health
    await page.keyboard.press("g");
    await page.keyboard.press("y");
    await expect(page.getByRole("heading", { name: "System Health" })).toBeVisible();
    await expect(page.getByTestId("ws-state")).toHaveText("open", { timeout: 15_000 });
    await expect(page.getByTestId("component-database")).toContainText("Operational");
    await expect(page.getByTestId("component-redis")).toContainText("Operational");
    await expect(page.getByTestId("component-worker")).toContainText("Operational", {
      timeout: 15_000,
    });
    await expect(page.getByTestId("component-market_data")).toContainText("Not configured");

    // Command palette -> Risk Guardian
    await page.keyboard.press("Control+k");
    await page.getByLabel("Search pages").fill("risk guard");
    await page.keyboard.press("Enter");
    await expect(page.getByRole("heading", { name: "Risk Guardian" })).toBeVisible();
    await expect(page.getByText(/Not available yet — scheduled for Phase 4/)).toBeVisible();

    await page.getByRole("button", { name: "Sign out" }).click();
    await expect(page).toHaveURL(/\/login$/);
    await page.goto("/");
    await expect(page).toHaveURL(/\/login$/);
  });

  test("all 14 dashboard pages render", async ({ page }) => {
    await page.goto("/login");
    await page.getByLabel("Email").fill(EMAIL);
    await page.getByLabel("Password").fill(PASSWORD);
    await page.getByRole("button", { name: "Sign in" }).click();
    await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();
    const pages: [string, string][] = [
      ["/radar", "Market Radar"], ["/opportunities", "Opportunity Leaderboard"],
      ["/charts", "Advanced Charts"], ["/copilot", "AI Copilot"],
      ["/account", "Apex Account Health"], ["/risk", "Risk Guardian"],
      ["/strategies", "Strategy Laboratory"], ["/backtesting", "Backtesting"],
      ["/journal", "Trading Journal"], ["/performance", "Performance Analytics"],
      ["/calendar", "Economic Calendar"], ["/settings", "Settings & Integrations"],
      ["/system", "System Health"],
    ];
    for (const [path, heading] of pages) {
      await page.goto(path);
      await expect(page.getByRole("heading", { name: heading, level: 1 })).toBeVisible();
    }
  });
});
