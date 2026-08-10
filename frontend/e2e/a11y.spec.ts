import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

import { signInAs, waitForProjects } from "./helpers";

/**
 * Surface-level axe scans of the highest-traffic pages.
 * Excludes rules we can't cleanly fix with third-party code:
 *   - `color-contrast` inside Monaco / Mermaid (their themes, not ours)
 */
const AXE_DISABLED = ["color-contrast-enhanced"];

test("login page is accessible", async ({ page }) => {
  await page.goto("/login");
  const results = await new AxeBuilder({ page })
    .disableRules(AXE_DISABLED)
    .analyze();
  expect(results.violations, JSON.stringify(results.violations, null, 2)).toEqual([]);
});

test("projects list is accessible", async ({ page }) => {
  await signInAs(page, "admin");
  await waitForProjects(page);
  const results = await new AxeBuilder({ page })
    .exclude(".monaco-editor") // Monaco's own DOM, not ours
    .disableRules(AXE_DISABLED)
    .analyze();
  expect(results.violations, JSON.stringify(results.violations, null, 2)).toEqual([]);
});

test("project overview is accessible", async ({ page }) => {
  await signInAs(page, "admin");
  await page.goto("/projects/ingest");
  await expect(page.getByRole("heading", { name: "Ingest pipeline" })).toBeVisible();
  const results = await new AxeBuilder({ page })
    .exclude(".monaco-editor")
    .disableRules(AXE_DISABLED)
    .analyze();
  expect(results.violations, JSON.stringify(results.violations, null, 2)).toEqual([]);
});
