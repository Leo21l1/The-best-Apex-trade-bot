import { expect, type Page, test } from "@playwright/test";

async function signIn(page: Page) {
  await page.goto("/login");
  await page.getByLabel("Email").fill("e2e-trader@example.com");
  await page.getByLabel("Password").fill("E2e-Correct-Horse-42!");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Market Analyzer", level: 1 })).toBeVisible();
}

function nyLocal(offsetMin: number): string {
  const z = new Date(new Date(Date.now() + offsetMin * 60000).toLocaleString("en-US", { timeZone: "America/New_York" }));
  const p = (n: number) => String(n).padStart(2, "0");
  return `${z.getFullYear()}-${p(z.getMonth() + 1)}-${p(z.getDate())}T${p(z.getHours())}:${p(z.getMinutes())}`;
}

test("news → idea → chart → plan → paper execution → close → journal → coaching → replay", async ({ page }) => {
  test.setTimeout(240_000);
  await signIn(page);

  // 1. Detect news: an event recorded in the calendar.
  await page.goto("/calendar");
  await page.getByLabel("Title").fill("Consumer Price Index (workflow)");
  await page.getByLabel("Date and time (New York)").fill(nyLocal(-40));
  await page.getByRole("button", { name: "Add", exact: true }).click();
  await expect(page.getByText("Event added.")).toBeVisible();

  // 2-3. Trade idea and news on the chart.
  await page.goto("/terminal");
  await page.getByLabel("Terminal instrument").selectOption("ES");
  await page.getByRole("button", { name: "5m", exact: true }).click();
  await expect(page.getByTestId("candle-chart")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("signal-badge")).toHaveText(/BUY|SHORT|WAIT|NO TRADE/);
  await expect(page.getByTestId("news-markers")).toContainText("Consumer Price Index (workflow)");
  await page.getByTestId("news-markers").getByRole("button").first().click();
  await expect(page.getByTestId("news-panel")).toContainText("no directional forecast");
  await page.getByRole("button", { name: "Close" }).click();
  await expect(page.getByTestId("live-disabled")).toBeDisabled();

  const status = await (await page.request.get("/api/market/status")).json();
  test.skip(!status.market_open, "Futures market closed: the simulator correctly rejects orders");

  // 4-5. Plan and paper execution.
  await page.getByLabel("Terminal instrument").selectOption("ES");
  await expect(page.getByTestId("terminal-last")).not.toHaveText("—", { timeout: 30_000 });
  const last = Number((await page.getByTestId("terminal-last").textContent())!.replace(/,/g, ""));
  const q = (x: number) => (Math.round(x * 4) / 4).toString();
  await page.getByLabel("Ticket stop").fill(q(last - 5));
  await page.getByLabel("Ticket target").fill(q(last + 10));
  await page.getByLabel("Ticket thesis").fill("Workflow test thesis");
  await expect(page.getByTestId("ticket-math")).toContainText("Reward:risk");
  await page.getByRole("button", { name: /BUY 1 ES — paper/ }).click();
  await expect(page.getByTestId("ticket-msg")).toContainText("sent to the paper simulator");

  // 6-7. Position tracked, then closed.
  await expect(page.getByTestId("terminal-positions")).toBeVisible({ timeout: 90_000 });
  await page.getByTestId("terminal-positions").getByRole("button", { name: "Close" }).click();
  await expect(page.getByTestId("terminal-positions")).toHaveCount(0, { timeout: 90_000 });

  // 8-10. Journal entry with P&L, generated journal and coaching.
  await page.goto("/journal?source=paper");
  await page.getByTestId("journal-table").getByRole("link", { name: "Review" }).first().click();
  await expect(page.getByTestId("trade-review")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("journal-sections")).toContainText("Workflow test thesis");
  await expect(page.getByTestId("journal-sections")).toContainText("Long 1 ES (simulated (paper))");
  await expect(page.getByTestId("scenarios")).toContainText("Followed the original plan");
  await page.getByRole("button", { name: "Generate AI review" }).click();
  await expect(page.getByText("What went well").first()).toBeVisible({ timeout: 20_000 });

  // 11. Replay: hindsight stays locked until the end.
  await page.getByRole("tab", { name: "Replay (decision view)" }).click();
  await expect(page.getByText("Locked during replay")).toBeVisible({ timeout: 20_000 });
  await page.getByLabel("Replay speed").selectOption("30");
  await page.getByTestId("replay-play").click();
  await expect(page.getByTestId("hindsight")).toContainText("Realised", { timeout: 60_000 });
});
