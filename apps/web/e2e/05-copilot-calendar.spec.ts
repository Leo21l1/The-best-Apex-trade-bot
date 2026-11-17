import { expect, type Page, test } from "@playwright/test";

async function signIn(page: Page) {
  await page.goto("/login");
  await page.getByLabel("Email").fill("e2e-trader@example.com");
  await page.getByLabel("Password").fill("E2e-Correct-Horse-42!");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();
}

const ICS = [
  "BEGIN:VCALENDAR", "VERSION:2.0",
  "BEGIN:VEVENT", "UID:e2e-cpi@bls.gov", "DTSTART:20991210T133000Z", "SUMMARY:Consumer Price Index", "END:VEVENT",
  "BEGIN:VEVENT", "UID:e2e-fomc@fed.gov", "DTSTART:20991216T190000Z", "SUMMARY:FOMC Rate Decision", "END:VEVENT",
  "END:VCALENDAR",
].join("\r\n");

test.describe("copilot, calendar and briefing", () => {
  test("copilot answers from real application data and shows its tool calls", async ({ page }) => {
    await signIn(page);
    await page.goto("/copilot");
    await expect(page.getByText("cannot place, modify or cancel orders")).toBeVisible();
    await page.getByLabel("Ask the copilot").fill("Are my rules verified and what is my risk budget?");
    await page.keyboard.press("Enter");
    const answer = page.getByTestId("copilot-answer").last();
    // 16 rules, one verified by the account spec -> 15 unverified (the fake counts every rule).
    await expect(answer).toContainText("Unverified Apex rules: 15", { timeout: 20_000 });
    // The same $350 the Risk Guardian computed in the account spec, read through the tool.
    await expect(answer).toContainText("Risk budget: $350.00");
    await expect(answer.getByRole("heading", { name: "Verified data" })).toBeVisible();
    await answer.getByText(/Data used: 2 read-only tool calls/).click();
    await expect(answer.getByText("get_account_risk()")).toBeVisible();
    // History survives a reload.
    await page.reload();
    await expect(page.getByText("Are my rules verified and what is my risk budget?")).toBeVisible();
  });

  test("calendar imports an official ICS file and lists classified events", async ({ page }) => {
    await signIn(page);
    await page.goto("/calendar");
    await expect(page.getByText("Apex news-trading restrictions are unverified")).toBeVisible();
    await page.getByLabel("ICS content").fill(ICS);
    await page.getByRole("button", { name: "Preview" }).click();
    await expect(page.getByTestId("ics-result")).toContainText("2 event(s) parsed");
    await page.getByRole("button", { name: /Import 2 events/ }).click();
    await expect(page.getByTestId("ics-result")).toContainText("Imported 2");
    // Events 70+ years out are beyond the 14-day view: the API keeps them, the page says so.
    await expect(page.getByText(/No events in the next 14 days|Upcoming events/).first()).toBeVisible();
  });

  test("overview shows the deterministic session briefing", async ({ page }) => {
    await signIn(page);
    const b = page.getByTestId("briefing");
    await expect(b).toBeVisible();
    await expect(b).toContainText("Apex rule profile UNVERIFIED");
    await expect(b.getByRole("cell", { name: "ES", exact: true })).toBeVisible({ timeout: 45_000 });
  });
});
