import { expect, test } from "@playwright/test";

import { signInAs } from "./helpers";

test.describe("auth", () => {
  test("unauthenticated user is redirected to /login", async ({ page }) => {
    await page.goto("/projects");
    await expect(page).toHaveURL(/\/login/);
    await expect(page.getByRole("heading", { name: /sign in to your workspace|continue/i }))
      .toBeVisible();
  });

  test("viewer sees no Audit log in the sidebar", async ({ page }) => {
    await signInAs(page, "viewer");
    await expect(
      page.getByRole("navigation", { name: "Primary" }).getByText("Audit log"),
    ).toHaveCount(0);
  });

  test("admin sees the full nav", async ({ page }) => {
    await signInAs(page, "admin");
    const sidebar = page.getByRole("navigation", { name: "Primary" });
    await expect(sidebar.getByText("Projects")).toBeVisible();
    await expect(sidebar.getByText("Runs")).toBeVisible();
    await expect(sidebar.getByText("Integrations")).toBeVisible();
    await expect(sidebar.getByText("Audit log")).toBeVisible();
  });

  test("sign out clears the session", async ({ page }) => {
    await signInAs(page, "admin");
    await page.getByRole("button", { name: /Account menu/i }).click();
    await page.getByRole("menuitem", { name: /Sign out/i }).click();
    await expect(page).toHaveURL(/\/login/);
  });
});
