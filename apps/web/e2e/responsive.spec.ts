import { expect, test } from "@playwright/test";

const PAGES = ["/", "/news", "/overview", "/radar", "/opportunities", "/charts", "/copilot", "/account", "/risk", "/journal",
  "/performance", "/strategies", "/research", "/backtesting", "/paper", "/terminal", "/trade", "/calendar", "/settings", "/system"];

test("mobile: every page fits the screen width and the tabs navigate", async ({ page }) => {
  test.setTimeout(120_000);
  await page.goto("/login");
  await page.getByLabel("Email").fill("e2e-trader@example.com");
  await page.getByLabel("Password").fill("E2e-Correct-Horse-42!");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Market Analyzer", level: 1 })).toBeVisible();

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
  await page.getByRole("navigation", { name: "Primary" }).getByRole("link", { name: "News" }).click();
  await expect(page.getByRole("heading", { name: "News", level: 1 })).toBeVisible();
  await page.getByRole("link", { name: "Settings and health" }).click();
  await expect(page.getByRole("heading", { name: "Settings & Integrations", level: 1 })).toBeVisible();
});
