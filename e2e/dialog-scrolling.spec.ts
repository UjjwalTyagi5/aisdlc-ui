import { test, expect as baseExpect, type Page } from "@playwright/test";

const expect = baseExpect.configure({ timeout: 20_000 });
test.describe.configure({ timeout: 120_000 });

/**
 * A long form in a dialog has to stay reachable.
 *
 * The failure this guards is silent and total: `DialogContent` had no
 * max-height, so a form taller than the viewport rendered centred, overflowed
 * both ends, and could not be scrolled to. The submit button was simply not
 * reachable — the dialog looked fine and the feature was unusable.
 */
async function signInAsOrgAdmin(page: Page) {
  await page.goto("/login");
  await page.locator("#platform-role-org_admin").check();
  await page.getByRole("button", { name: "Continue as Organization Admin" }).click();
  await page.waitForURL((url) => !url.pathname.startsWith("/login"), { waitUntil: "commit" });
  await page.waitForLoadState("domcontentloaded");
}

test.beforeAll(async ({ browser }) => {
  test.setTimeout(240_000);
  const page = await browser.newPage();
  await signInAsOrgAdmin(page);
  await page.goto("/workspaces").catch(() => undefined);
  await page.getByRole("heading").first().waitFor({ timeout: 60_000 }).catch(() => undefined);
  await page.close();
});

test("the create business unit form fits the viewport and keeps Create reachable", async ({
  page,
}) => {
  // A short viewport is the point: the form is far taller than this.
  await page.setViewportSize({ width: 1280, height: 620 });
  await signInAsOrgAdmin(page);
  await page.goto("/workspaces");

  await page.getByRole("button", { name: /new business unit/i }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();

  // The dialog fits inside the viewport, top and bottom. Polled rather than
  // measured once: the open animation slides in from above, so a single
  // boundingBox() reads a position the dialog is still travelling through.
  await expect
    .poll(async () => {
      const b = await dialog.boundingBox();
      if (!b) return "no box";
      if (b.height > 620) return `too tall: ${Math.round(b.height)}`;
      if (b.y < 0) return `above the fold: ${Math.round(b.y)}`;
      if (b.y + b.height > 620) return `below the fold: ${Math.round(b.y + b.height)}`;
      return "fits";
    })
    .toBe("fits");

  // The footer is pinned, so the primary action is visible without scrolling
  // — it was previously below the fold with nothing indicating it existed.
  const create = dialog.getByRole("button", { name: /^Create business unit$/i });
  await expect(create).toBeInViewport();

  // And the body genuinely scrolls: the admin email field lives at the bottom.
  const email = dialog.getByPlaceholder("name@company.com");
  await email.scrollIntoViewIfNeeded();
  await expect(email).toBeInViewport();
  // Scrolling the body must not have pushed the footer away.
  await expect(create).toBeInViewport();
});

test("the org-wide viewer is not shown a bare ORGANIZATION scope chip", async ({ page }) => {
  // It restated the default on every screen: an org-wide viewer has no
  // narrower scope for their numbers to be confused with.
  await signInAsOrgAdmin(page);

  for (const path of ["/approvals", "/workspaces", "/users", "/cost"]) {
    await page.goto(path);
    await expect(page.getByText("Organization", { exact: true })).toHaveCount(0);
  }
});
