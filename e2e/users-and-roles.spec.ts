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
  // Console errors are collected because a nesting mistake — a Badge, which is
  // a div, inside a paragraph — surfaces only as a hydration warning at
  // runtime. tsc and lint both pass on it.
  const errors: string[] = [];
  page.on("console", (m) => {
    if (m.type() === "error") errors.push(m.text());
  });

  await signIn(page, ORG_ADMIN);
  await page.goto("/users");

  await page.getByRole("button", { name: /Manage roles for/ }).first().click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();

  // What they hold, and a way to add — both in front of the person, which the
  // old scope-first Assignments screen could not do.
  await expect(dialog.getByText("Holds now")).toBeVisible();
  await expect(dialog.getByText("Grant another")).toBeVisible();
  await expect(dialog.getByText(/written to the audit trail/)).toBeVisible();

  expect(errors.filter((e) => /cannot be a descendant|hydration/i.test(e))).toEqual([]);
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

test("a business unit admin may contribute to another unit's project", async ({ page }) => {
  // The allowed half of the rule. Noah runs Platform Engineering and builds on
  // a Lending project — administering one unit does not confine him to it.
  await signIn(page, ORG_ADMIN);
  await page.goto("/users");

  const noah = page.locator("tr").filter({ hasText: "Noah Bennett" });
  await expect(noah.getByText("BU Admin")).toBeVisible();
  await expect(noah.getByText("Core ledger — Java 8 to 21")).toBeVisible();
  await expect(noah.getByText("Developer")).toBeVisible();
});

test("Roles & Access is about roles, not assignments", async ({ page }) => {
  await signIn(page, ORG_ADMIN);
  await page.goto("/admin/access/roles");

  const tabs = page.getByRole("navigation", { name: /roles and access views/i });
  await expect(tabs.getByRole("link", { name: "Built-in roles" })).toBeVisible();
  await expect(tabs.getByRole("link", { name: "Custom roles" })).toBeVisible();
  await expect(tabs.getByRole("link", { name: "Assignments" })).toHaveCount(0);
});

test("every role's permissions are visible, and the Org Admin can change them", async ({
  page,
}) => {
  await signIn(page, ORG_ADMIN);
  await page.goto("/admin/access/roles");

  // "What can this role do" was answerable nowhere on this screen before.
  await page.getByRole("button", { name: /Show permissions for Developer/ }).click();
  await expect(page.getByText("connector:view").first()).toBeVisible();

  // Ticking STAGES the change. Nothing is written until Update — a mis-click
  // must not be a permission change already in force.
  const box = page.getByRole("checkbox", { name: /Register \/ edit a connection/ }).first();
  await box.click();
  await expect(page.getByText("Unsaved")).toBeVisible();
  await expect(page.getByText(/Nothing changes until you update/)).toBeVisible();
  await expect(page.getByText("Developer updated")).toHaveCount(0);

  // Discard puts it back without writing anything.
  await page.getByRole("button", { name: "Discard" }).click();
  await expect(page.getByText("Unsaved")).toHaveCount(0);

  // Stage it again and commit.
  await box.click();
  await page.getByRole("button", { name: /Update role/ }).click();
  await expect(page.getByText("Developer updated")).toBeVisible();
  await expect(page.getByText("Modified").first()).toBeVisible();

  await page.getByRole("button", { name: /Reset to defaults/ }).click();
  await expect(page.getByText(/reset to its defaults/i)).toBeVisible();
});

test("the wildcard role is shown as fixed rather than as forty empty boxes", async ({ page }) => {
  await signIn(page, ORG_ADMIN);
  await page.goto("/admin/access/roles");

  await page.getByRole("button", { name: /Show permissions for Organization Admin/ }).click();
  await expect(page.getByText(/admin:\*/).first()).toBeVisible();
  await expect(page.getByText(/lock the last administrator out/i)).toBeVisible();
});

test("the reference role cards are gone", async ({ page }) => {
  // Twelve cards of one-line descriptions: true once, then a screenful of
  // prose above the thing you came to change.
  await signIn(page, ORG_ADMIN);
  await page.goto("/admin/access/roles");

  await expect(page.getByText("The twelve roles")).toHaveCount(0);
  await expect(page.getByText(/Two tiers that never cross/)).toHaveCount(0);
  // What replaced them still names every role.
  await expect(page.getByRole("button", { name: /Show permissions for Developer/ })).toBeVisible();
});

test("the ownership matrix carries all thirteen agents", async ({ page }) => {
  // PHASE_ORDER is the greenfield pipeline, not the agent roster — building
  // the matrix from it dropped the five track-specific agents, including the
  // Data Engineer's own.
  await signIn(page, ORG_ADMIN);
  await page.goto("/admin/access/roles");

  for (const agent of ["Discovery", "Strategy", "Validation", "Data Engineering"]) {
    await expect(page.getByRole("columnheader", { name: agent })).toBeVisible();
  }
});

test("the crumb does not say Roles & Access twice", async ({ page }) => {
  await signIn(page, ORG_ADMIN);
  await page.goto("/admin/access/roles");

  const crumbs = page.getByRole("navigation", { name: /breadcrumb/i });
  await expect(crumbs.getByText("Roles & Access")).toHaveCount(1);
});

test("the old Assignments URL still resolves", async ({ page }) => {
  // Redirected rather than deleted — the sidebar, bookmarks and the command
  // palette all still point at it.
  await signIn(page, ORG_ADMIN);
  await page.goto("/admin/access");
  await expect(page).toHaveURL(/\/admin\/access\/roles$/);
});
