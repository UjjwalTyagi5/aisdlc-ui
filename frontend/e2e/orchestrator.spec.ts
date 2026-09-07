import { expect, test, type Page } from "@playwright/test";

/**
 * The Orchestrator (`/orchestrator`), rewritten for what it actually is.
 *
 * The previous version of this file was written against the scripted mock engine — a
 * fixed nine-step pipeline with hard-coded replies and auto-approving gates — and was
 * skipped wholesale in Phase 1 when that engine was deleted, retained as "the
 * specification for what the Phase 3 rewrite must cover". Phases 3, 4 and 5 have all
 * landed, so this is that rewrite.
 *
 * WHAT CHANGED, and why almost none of the old assertions could survive:
 *   · There is no pipeline and no ordering. Any agent can run at any time, chosen from
 *     the conversation — so "runs the roster stage by stage" describes nothing.
 *   · There are no gates and no sign-off (D5), so every gate assertion is gone.
 *   · There is no "Run the pipeline" button; the surface is a chat.
 *   · The right panel is Deliverables, not Artifacts (D11).
 *   · The Copilot it replaced no longer exists (D18).
 *
 * SCOPE. This runs in the default `chromium` project, which boots with MSW mocks and no
 * backend, so it covers access control and the surface — not a live agent turn. Driving
 * a real turn needs the real-api project, a seeded tenant and BYOK model calls; the
 * backend equivalents are `scripts/live_deliverables_check.py`,
 * `scripts/live_sessions_check.py` and `scripts/live_routing_check.py`, which exercise
 * that path against a real database and a real model.
 */

async function signInAsPlatformRole(page: Page, label: RegExp) {
  await page.goto("/login");
  await page.getByRole("radio", { name: label }).check();
  await page.getByRole("button", { name: /continue as/i }).click();
  await page.waitForURL((url) => !url.pathname.startsWith("/login"), {
    waitUntil: "commit",
  });
  await page.waitForLoadState("networkidle");
}

/**
 * Open the Orchestrator, tolerating the cold-compile race.
 *
 * The first test to reach this route pays Next's dev compile. A `goto` issued while the
 * previous page still has requests in flight is reported as ERR_ABORTED rather than
 * retried, so navigate on `domcontentloaded` and give it one more go.
 */
async function gotoOrchestrator(page: Page) {
  for (let attempt = 0; attempt < 2; attempt++) {
    try {
      await page.goto("/orchestrator", { waitUntil: "domcontentloaded" });
      return;
    } catch (err) {
      if (attempt === 1) throw err;
    }
  }
}

test.describe("Orchestrator — the merged surface", () => {
  test("a Project Admin reaches it", async ({ page }) => {
    await signInAsPlatformRole(page, /^Project Admin\b/i);
    await gotoOrchestrator(page);

    // The composer is the surface. It is DISABLED until a project is picked — the
    // mock persona has no business unit — but present, which is what distinguishes
    // "admitted, pick a project" from "refused".
    await expect(page.getByRole("textbox", { name: /message the orchestrator/i }))
      .toBeVisible({ timeout: 30_000 });
    await expect(page.getByRole("heading", { name: /pick a project to orchestrate/i }))
      .toBeVisible();
    await expect(page.getByText(/you do not have access/i)).toHaveCount(0);
  });

  test("the right panel is Deliverables, not Artifacts", async ({ page }) => {
    // D11: what the Orchestrator's agents produce is a different concept from the
    // approval-gated artifacts the standalone agents write, and the tab says so.
    await signInAsPlatformRole(page, /^Project Admin\b/i);
    await gotoOrchestrator(page);

    // The panel is xl-and-up and starts collapsed, so what is on screen is its rail.
    await page.setViewportSize({ width: 1600, height: 900 });
    await expect(page.getByRole("textbox", { name: /message the orchestrator/i }))
      .toBeVisible({ timeout: 30_000 });

    const deliverables = page.getByRole("tab", { name: /^deliverables$/i })
      .or(page.getByRole("button", { name: /show deliverables panel/i }));
    await expect(deliverables.first()).toBeVisible({ timeout: 15_000 });
    // And never the retired name on this surface.
    await expect(page.getByRole("tab", { name: /^artifacts$/i })).toHaveCount(0);
  });

  test("there is no pipeline rail and no gate controls", async ({ page }) => {
    // "There won't be a linearity that, after one agent, the next comes." Any agent can
    // run at any time, so nothing on this surface may imply an order or a sign-off.
    await signInAsPlatformRole(page, /^Project Admin\b/i);
    await gotoOrchestrator(page);
    await page.waitForLoadState("networkidle");

    await expect(page.getByRole("button", { name: /^run the pipeline$/i })).toHaveCount(0);
    await expect(page.getByRole("button", { name: /approve & continue/i })).toHaveCount(0);
    await expect(page.getByText(/auto-advance/i)).toHaveCount(0);
    await expect(page.getByText(/awaiting sign-off/i)).toHaveCount(0);
  });

  test("the chat history rail is not described as browser-only", async ({ page }) => {
    // Phase 5B moved sessions server-side. The rail used to say "Sessions are stored in
    // this browser only", which stopped being true.
    await signInAsPlatformRole(page, /^Project Admin\b/i);
    await gotoOrchestrator(page);
    await page.waitForLoadState("networkidle");

    await expect(page.getByText(/stored in this browser only/i)).toHaveCount(0);
    await expect(page.getByText(/open on any device/i)).toBeVisible();
  });

  test("a delivery role is refused", async ({ page }) => {
    // Driving the Orchestrator IS holding every agent's access at once, so it is
    // Project-Admin-only. A developer reaching the URL directly must be refused — this
    // is the bug the Phase 1 whole-branch review found on exactly one of four surfaces.
    await signInAsPlatformRole(page, /^Developer\b/i);
    await gotoOrchestrator(page);
    await page.waitForLoadState("networkidle");

    // Refused outright: no composer at all, not merely a disabled one.
    await expect(page.getByRole("textbox", { name: /message the orchestrator/i }))
      .toHaveCount(0);
    await expect(page.getByRole("heading", { name: /pick a project to orchestrate/i }))
      .toHaveCount(0);
  });
});

test.describe("the Copilot is gone", () => {
  test("its page no longer exists", async ({ page }) => {
    // Phase 1 unlinked it and deliberately left it reachable, because it was the only
    // surface actually running agents. Phase 5 deleted it once that stopped being true.
    await signInAsPlatformRole(page, /^Project Admin\b/i);

    const response = await page.goto("/projects/p1/copilot", {
      waitUntil: "domcontentloaded",
    });
    // Either a 404 from the router, or Next's not-found page. What must NOT happen is
    // the Copilot rendering.
    await expect(page.getByRole("button", { name: /^run the pipeline$/i })).toHaveCount(0);
    expect(response?.status()).not.toBe(200);
  });
});
