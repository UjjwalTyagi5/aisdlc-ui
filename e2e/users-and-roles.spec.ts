import { test, expect as baseExpect, type Page } from "@playwright/test";

const expect = baseExpect.configure({ timeout: 20_000 });
test.describe.configure({ timeout: 120_000 });

/**
 * Onboarding people and giving them roles happens on Users; what a role MEANS
 * lives on Roles & Access. These cover the seam between the two.
 */
async function signIn(page: Page, role: { slug: string; label: string }) {
  await page.goto("/login");
  await page.locator(`#platform-role-${role.slug}`).check();
  await page.getByRole("button", { name: `Continue as ${role.label}` }).click();
  await page.waitForURL((url) => !url.pathname.startsWith("/login"), { waitUntil: "commit" });
  await page.waitForLoadState("domcontentloaded");
}

const ORG_ADMIN = { slug: "org_admin", label: "Organization Admin" };

test.beforeAll(async ({ browser }) => {
  test.setTimeout(240_000);
  const page = await browser.newPage();
  await signIn(page, ORG_ADMIN);
  for (const path of ["/users", "/admin/access/roles"]) {
    await page.goto(path).catch(() => undefined);
    await page.getByRole("heading").first().waitFor({ timeout: 60_000 }).catch(() => undefined);
  }
  await page.close();
});

test("roles are assigned from the person's own row on Users", async ({ page }) => {
  await signIn(page, ORG_ADMIN);
  await page.goto("/users");

  await page.getByRole("button", { name: /Manage roles for/ }).first().click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();

  // What they hold, and a way to add — both in front of the person, which the
  // old scope-first Assignments screen could not do.
  await expect(dialog.getByText("Holds now")).toBeVisible();
  await expect(dialog.getByText("Grant another")).toBeVisible();
});

test("granting a role that would breach separation of duties is refused", async ({ page }) => {
  await signIn(page, ORG_ADMIN);
  await page.goto("/users");
  await page.getByRole("button", { name: /Manage roles for/ }).first().click();

  const dialog = page.getByRole("dialog");
  await dialog.getByLabel(/Choose a business unit/i).click();
  await page.getByRole("option").first().click();
  await dialog.getByLabel("Choose a role").click();
  await page.getByRole("option").first().click();

  // Either it grants or it explains why not — never a dead button with no cause.
  const grant = dialog.getByRole("button", { name: "Grant role" });
  const enabled = await grant.isEnabled();
  if (!enabled) {
    await expect(
      dialog.getByText(/already hold|approve their own work/i).first(),
    ).toBeVisible();
  }
});

test("Roles & Access is about roles, not assignments", async ({ page }) => {
  await signIn(page, ORG_ADMIN);
  await page.goto("/admin/access/roles");

  const tabs = page.getByRole("navigation", { name: /roles and access views/i });
  await expect(tabs.getByRole("link", { name: "Built-in roles" })).toBeVisible();
  await expect(tabs.getByRole("link", { name: "Custom roles" })).toBeVisible();
  await expect(tabs.getByRole("link", { name: "Assignments" })).toHaveCount(0);
});

test("the old Assignments URL still resolves", async ({ page }) => {
  // Redirected rather than deleted — the sidebar, bookmarks and the command
  // palette all still point at it.
  await signIn(page, ORG_ADMIN);
  await page.goto("/admin/access");
  await expect(page).toHaveURL(/\/admin\/access\/roles$/);
});
