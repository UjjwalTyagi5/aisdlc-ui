/**
 * END-TO-END, AGAINST THE REAL BACKEND. Two changes, validated as a user meets them:
 *
 *   1. A Business Unit Admin onboards someone into their own unit, naming the role.
 *   2. Projects render as a table for an admin, and as cards for everybody else.
 *
 * Runs against `next dev` in real-backend mode (.env.local: NEXT_PUBLIC_API_MOCKS=off,
 * NEXT_PUBLIC_AUTH_MODE=local) with FastAPI on :8001 and the dev personas seeded
 * (`python -m scripts.seed_dev_personas`). Nothing here is mocked — the onboarding POST
 * reaches shared/routers/onboarding.py and writes a real role_binding.
 *
 * Personas (DEV_LOGINS.txt, all `devpassword123`):
 *   orgadmin@abcbank.com  org_admin
 *   farah@abcbank.com     bu_admin, Payments
 *   diego@abcbank.com     developer on a project — the "everyone else" case
 */
import { test, expect, type Page } from "@playwright/test";

const PASSWORD = "devpassword123";

async function signIn(page: Page, email: string) {
  await page.goto("/login");
  // By id, not by label: getByLabel(/password/i) also matches the "Show password"
  // toggle button sitting inside the same field.
  await page.locator("#email").fill(email);
  await page.locator("#password").fill(PASSWORD);
  await page.getByRole("button", { name: /^sign in$/i }).click();
  await page.waitForURL((url) => !url.pathname.startsWith("/login"), { waitUntil: "commit" });
  await page.waitForLoadState("domcontentloaded");
}

test.describe("a Business Unit Admin onboards into their own unit", () => {
  test("the button is there, and onboarding grants the role immediately", async ({ page }) => {
    await signIn(page, "farah@abcbank.com");
    await page.goto("/users");

    // 1. The button a BU Admin never used to have. The generous timeout is the
    //    access scope: it arrives from a query, and until it lands the page cannot
    //    know how many units this person administers.
    const onboard = page.getByRole("button", { name: /onboard someone/i });
    await expect(onboard, "a BU Admin should now see the direct onboarding button")
      .toBeVisible({ timeout: 20_000 });

    // 2. And NOT the one it replaces — two routes to one outcome, one of which
    //    needed an Org Admin to press a button. Asserted AFTER the scope has landed,
    //    because before it the page rightly shows neither.
    await expect(page.getByRole("button", { name: /request onboarding/i })).toHaveCount(0);

    // 3. Bulk upload stays an Org Admin's: a roster spans units.
    await expect(page.getByRole("button", { name: /bulk upload/i })).toHaveCount(0);

    await onboard.click();

    // Scope every selector to the dialog: the page behind it has its own search box
    // and its own per-row "Assign role" buttons, and an unscoped getByLabel finds them.
    const dialog = page.getByRole("dialog");
    const email = `e2e-dev-${Date.now()}@abcbank.com`;
    await dialog.locator("#onboard-email").fill(email);
    await dialog.getByRole("button", { name: /continue/i }).click();

    // Step 2 — the scoped form. The unit is shown as a statement (Farah administers
    // exactly one), and there are no org-level role cards.
    await expect(dialog.getByText("Payments", { exact: true })).toBeVisible();
    await expect(
      dialog.getByRole("button", { name: /runs a .*unit|works in one/i }),
      "the org-level role cards belong to the Org Admin's dialog only",
    ).toHaveCount(0);

    // Pick the role. Radix Select renders a button trigger, not a <select>, and its
    // options portal outside the dialog — so the listbox is looked up on the page.
    await dialog.locator("#onboard-unit-role").click();
    await page.getByRole("option", { name: "Developer", exact: true }).click();

    await dialog.getByRole("button", { name: /^onboard$/i }).click();

    // 4. THE RESULT, asserted on the durable state rather than the toast. A toast is
    //    gone in seconds and its absence proves nothing; the directory row is what a
    //    person would still see tomorrow, and it is written by the real backend.
    await expect(dialog).toHaveCount(0, { timeout: 20_000 });

    const row = page.getByRole("row").filter({ hasText: email });
    await expect(row, "the person the BU Admin just onboarded").toBeVisible({ timeout: 20_000 });

    // 5. Holding the role IMMEDIATELY — the whole point of naming it inline. The
    //    Org Admin's contributor path lands people on "No role yet" instead, and
    //    that difference is what this asserts.
    await expect(row).toContainText("Developer");
    await expect(row).not.toContainText(/no role yet/i);
    await expect(row).toContainText("Payments");
  });
});

test.describe("projects render as a table for admins only", () => {
  test("a Business Unit Admin lands on the table by default", async ({ page }) => {
    await signIn(page, "farah@abcbank.com");
    await page.goto("/projects");

    // The table IS the default — no ?view= in the URL.
    await expect(page.getByRole("table").first()).toBeVisible({ timeout: 15_000 });
    await expect(page.getByRole("columnheader", { name: "Project" })).toBeVisible();
    await expect(page.getByRole("columnheader", { name: "Status" })).toBeVisible();
    await expect(page.getByRole("columnheader", { name: "Updated" })).toBeVisible();

    // And the toggle can leave it, then come back.
    await page.getByRole("button", { name: /grid view/i }).click();
    await expect(page.getByRole("table")).toHaveCount(0);
    await page.getByRole("button", { name: /table view/i }).click();
    await expect(page.getByRole("table").first()).toBeVisible();
  });

  test("an Organization Admin gets the same default", async ({ page }) => {
    await signIn(page, "orgadmin@abcbank.com");
    await page.goto("/projects");
    await expect(page.getByRole("table").first()).toBeVisible({ timeout: 15_000 });
  });

  test("a non-admin gets cards, and cannot be forced into the table by a URL", async ({ page }) => {
    await signIn(page, "diego@abcbank.com");

    await page.goto("/projects");
    await expect(page.getByRole("button", { name: /table view/i })).toHaveCount(0);
    await expect(page.getByRole("table")).toHaveCount(0);

    // The bookmark case: an admin's ?view=table link opened by a contributor would
    // otherwise render a view with no toggle to leave it by.
    await page.goto("/projects?view=table");
    await expect(page.getByRole("table")).toHaveCount(0);
    await expect(page.getByRole("button", { name: /table view/i })).toHaveCount(0);
  });
});
