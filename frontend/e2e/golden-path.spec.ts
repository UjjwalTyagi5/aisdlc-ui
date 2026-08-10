import { expect, test } from "@playwright/test";

import { signInAs, waitForProjects } from "./helpers";

/**
 * Golden path — walks the MVP-0 flow end-to-end against MSW mocks.
 *
 *  sign in → projects → ingest overview → requirements → approve story →
 *  design → approve artifact → development (PR) → testing → approve test set
 */
test("golden path: requirements → design → dev → testing approvals", async ({ page }) => {
  await signInAs(page, "admin");
  await waitForProjects(page);

  // Projects list → open Ingest
  await page.getByRole("link", { name: /Ingest pipeline/i }).click();
  await expect(page).toHaveURL(/\/projects\/ingest/);
  await expect(page.getByRole("heading", { name: "Ingest pipeline" })).toBeVisible();

  // ── Requirements ────────────────────────────────────────
  await page.getByRole("link", { name: /Open Requirements/i }).click();
  await expect(page).toHaveURL(/\/projects\/ingest\/requirements/);

  // The filter-batches story lands awaiting approval per fixtures.
  await page.getByRole("option", { name: /Filter batches by status/i }).click();
  await expect(
    page.getByRole("heading", { name: /Gate: PM review/i }),
  ).toBeVisible();

  // Approve via keyboard — tests the `a` shortcut the acceptance criteria call out
  await page.keyboard.press("a");
  await expect(
    page.getByRole("status").filter({ hasText: /Approved/i }).first(),
  ).toBeVisible({ timeout: 5_000 });

  // ── Design ──────────────────────────────────────────────
  await page.goto("/projects/ingest/design");
  // The OpenAPI spec is awaiting approval. Select it in the artifact list.
  await page
    .getByRole("option", { name: /Ingest API — v1\.2 contract/i })
    .click();
  await expect(
    page.getByRole("heading", { name: /Gate: Architect \/ TL approval/i }),
  ).toBeVisible();
  await page.keyboard.press("a");
  await expect(
    page.getByRole("status").filter({ hasText: /Approved/i }).first(),
  ).toBeVisible({ timeout: 5_000 });

  // ── Development ─────────────────────────────────────────
  await page.goto("/projects/ingest/development");
  // The only PR on ingest is awaiting approval
  await page
    .getByRole("tab", { name: /^Review/ })
    .click()
    .catch(() => {});
  await page
    .getByRole("button", { name: /idempotent batch replay/i })
    .first()
    .click();
  await expect(
    page.getByRole("heading", { name: /Gate: developer \/ TL review/i }),
  ).toBeVisible();
  await page.keyboard.press("a");
  await expect(
    page.getByRole("status").filter({ hasText: /Approved/i }).first(),
  ).toBeVisible({ timeout: 5_000 });

  // ── Testing ─────────────────────────────────────────────
  await page.goto("/projects/ingest/testing");
  await expect(
    page.getByRole("heading", { name: /Gate: QA approval/i }),
  ).toBeVisible();
  await page.keyboard.press("a");
  await expect(
    page.getByRole("status").filter({ hasText: /Approved/i }).first(),
  ).toBeVisible({ timeout: 5_000 });

  // Final audit — every phase approved, no errors surfaced.
  // A global error boundary would render an <ErrorState role="alert"> — assert its
  // absence across the journey.
  const alerts = page.getByRole("alert", { name: /something went wrong/i });
  await expect(alerts).toHaveCount(0);
});
