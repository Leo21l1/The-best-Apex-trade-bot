import { expect, test } from "@playwright/test";

const PAGES = ["/", "/radar", "/opportunities", "/charts", "/copilot", "/account", "/risk", "/journal",
  "/performance", "/strategies", "/backtesting", "/paper", "/terminal", "/trade", "/calendar", "/settings", "/system"];

test("mobile: every page fits the screen width and the drawer navigates", async ({ page }) => {
  test.setTimeout(120_000);
  await page.goto("/login");
  await page.getByLabel("Email").fill("e2e-trader@example.com");
  await page.getByLabel("Password").fill("E2e-Correct-Horse-42!");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();

  const overflowing: string[] = [];
  for (const path of PAGES) {
    await page.goto(path);
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    await page.waitForTimeout(600); // let data-driven panels render
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    if (overflow > 0) overflowing.push(`${path} (+${overflow}px)`);
  }
  expect(overflowing).toEqual([]);

  await page.goto("/");
  await page.getByRole("button", { name: "Open navigation" }).click();
  await page.getByRole("navigation", { name: "Primary" }).getByRole("link", { name: /System Health/ }).click();
  await expect(page.getByRole("heading", { name: "System Health" })).toBeVisible();
});
