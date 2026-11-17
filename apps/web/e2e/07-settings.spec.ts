import { expect, type Page, test } from "@playwright/test";

async function signIn(page: Page, email: string, password: string) {
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();
}

test("admin adds a viewer, who can read but not change data", async ({ page }) => {
  await signIn(page, "e2e-trader@example.com", "E2e-Correct-Horse-42!");
  await page.goto("/settings");
  await page.getByLabel("New user email").fill("e2e-viewer@example.com");
  await page.getByLabel("New user password").fill("Viewer-Pass-42!");
  await page.getByRole("button", { name: "Add" }).click();
  await expect(page.getByTestId("users")).toContainText("e2e-viewer@example.com (viewer)");
  await page.getByRole("button", { name: "Sign out" }).click();

  await signIn(page, "e2e-viewer@example.com", "Viewer-Pass-42!");
  await expect(page.getByText("Read-only", { exact: true })).toBeVisible();
  await page.goto("/account");
  await expect(page.getByTestId("account-stats")).toBeVisible(); // can read
  await page.getByLabel("Balance (closed)").fill("51000");
  await page.getByRole("button", { name: "Save snapshot" }).click();
  await expect(page.getByText("Admin role required")).toBeVisible(); // cannot write
});
