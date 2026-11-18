import { expect, type Page, test } from "@playwright/test";

async function signIn(page: Page) {
  await page.goto("/login");
  await page.getByLabel("Email").fill("e2e-trader@example.com");
  await page.getByLabel("Password").fill("E2e-Correct-Horse-42!");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Market Analyzer", level: 1 })).toBeVisible();
}

test("edit a journal trade, save, reload and see it everywhere; notes; review opens", async ({ page }) => {
  test.setTimeout(120_000);
  page.on("dialog", (d) => void d.accept());
  await signIn(page);
  await page.goto("/journal");
  const firstEdit = page.getByTestId("journal-table").getByRole("button", { name: /^Edit trade/ }).first();
  await firstEdit.click();
  const editor = page.getByTestId("trade-editor");
  await expect(editor).toBeVisible();
  await editor.getByLabel("Edit setup").fill("ORB retest");
  await editor.getByLabel("Edit thesis and entry reasoning").fill("Held above the opening range");
  await editor.getByLabel("Edit fees and commissions").fill("9.99");
  await editor.getByRole("button", { name: "Save Changes" }).click();
  await expect(editor.getByRole("status")).toContainText("Saved");
  await expect(editor.getByTestId("record-provenance")).toContainText("with manual corrections");
  await expect(editor.getByTestId("original-values")).toContainText("fees=");
  await expect(editor.getByTestId("history")).toContainText("fees");

  // Notes: add, then delete with confirmation.
  await editor.getByLabel("New note").fill("Waited for the retest");
  await editor.getByRole("button", { name: "Add note" }).click();
  await expect(editor.getByTestId("note-list")).toContainText("Waited for the retest");
  await editor.getByTestId("note-list").getByRole("button", { name: "Delete" }).click();
  await expect(editor.getByTestId("note-list")).not.toContainText("Waited for the retest");
  await editor.getByRole("button", { name: "Close editor" }).click();

  // Persisted after a full reload.
  await page.reload();
  await expect(page.getByTestId("journal-table")).toContainText("corrected");
  await page.getByTestId("journal-table").getByRole("button", { name: /^Edit trade/ }).first().click();
  await expect(page.getByTestId("trade-editor").getByLabel("Edit setup")).toHaveValue("ORB retest");
  await page.getByTestId("trade-editor").getByRole("button", { name: "Close editor" }).click();

  // Trade review for the same trade loads (no internal server error) with facts at entry.
  await page.getByTestId("journal-table").getByRole("link", { name: "Review" }).first().click();
  await expect(page.getByTestId("trade-review")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText("Could not load the trade")).toHaveCount(0);
  await expect(page.getByTestId("session-at-entry")).toContainText("Session at entry");
});

test("readiness panel on Advanced Charts and Smart Money Analysis", async ({ page }) => {
  await signIn(page);
  await page.goto("/charts");
  const panel = page.getByTestId("readiness");
  await expect(panel).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("readiness-status")).toHaveText(/WAIT|REVIEW SETUP|NO TRADE/);
  for (const key of ["kill_zone", "not_choppy", "volume", "outside_lunch", "setup", "alignment", "reward_risk", "vwap_crosses"]) {
    await expect(page.getByTestId(`check-${key}`)).toContainText(/PASS|FAIL|WAIT|UNKNOWN/);
  }
  await expect(page.getByTestId("readiness-ny")).toContainText(/E[SD]T/);
  await page.getByTestId("check-volume").click();
  await expect(panel).toContainText(/Method:|available/);
  await page.getByLabel("Trading style").selectOption("afternoon");
  await expect(page.getByTestId("check-kill_zone")).toContainText(/afternoon|closed|Market closed/i);
  await page.goto("/smc");
  await expect(page.getByTestId("readiness")).toBeVisible({ timeout: 30_000 });
});
