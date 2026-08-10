import { expect, type Page } from "@playwright/test";

export type Role = "admin" | "member" | "viewer";

/**
 * Drives the mock-mode login panel at /login and lands the user on
 * /projects (or wherever the `from` param pointed).
 */
export async function signInAs(page: Page, role: Role = "admin") {
  await page.goto("/login");
  // The mock panel now defaults to the real 12-platform-role picker; this
  // helper drives the legacy 3-item admin/member/viewer picker, so switch to
  // it first via its "Use the legacy quick picker instead" toggle.
  await page.getByRole("button", { name: /legacy quick picker/i }).click();
  // The mock panel renders a Radix RadioGroup; each role radio's accessible name
  // is the role label + description (e.g. "Admin Full access — ..."), so match by
  // the leading word rather than an exact label.
  await page.getByRole("radio", { name: new RegExp(`^${role}\\b`, "i") }).check();
  // The mock-signin form POSTs and 303-redirects (/login -> / -> /projects). Don't
  // race waitForURL inside Promise.all against the click and don't wait for "load"
  // — the redirect chain aborts that ("frame was detached"). Click, then wait until
  // any non-login URL has *committed*.
  await page.getByRole("button", { name: /continue as/i }).click();
  await page.waitForURL((url) => !url.pathname.startsWith("/login"), {
    waitUntil: "commit",
  });
  await page.waitForLoadState("domcontentloaded");
}

/**
 * Waits until MSW has injected the page data — the TanStack Query result for
 * the first card is the most reliable signal on the Projects list.
 */
export async function waitForProjects(page: Page) {
  await expect(page.getByRole("heading", { name: "Projects" })).toBeVisible();
  await expect(page.getByText("Ingest pipeline").first()).toBeVisible();
}
