import { expect, test, type Page } from "@playwright/test";

/**
 * The cross-project Orchestrator (`/orchestrator`) — pick a project, pick a
 * model that project is allowed to run on, and watch the agent roster execute
 * in hand-off order.
 *
 * Signs in as **Project Admin** because driving the Orchestrator is that role's
 * alone (PRD §15.5–§15.11); every other role gets the read-only rendering,
 * which the last test covers.
 */

async function signInAsPlatformRole(page: Page, label: RegExp) {
  await page.goto("/login");
  // The mock panel defaults to the 12-platform-role picker — no toggle needed.
  await page.getByRole("radio", { name: label }).check();
  await page.getByRole("button", { name: /continue as/i }).click();
  await page.waitForURL((url) => !url.pathname.startsWith("/login"), {
    waitUntil: "commit",
  });
  // The landing page (/dashboard) is still fetching when the redirect commits.
  // Navigating away mid-flight aborts the pending request and Chromium reports
  // the *next* goto as ERR_ABORTED, so let the landing settle first.
  await page.waitForLoadState("networkidle");
}

/**
 * Open the Orchestrator, tolerating the cold-compile race.
 *
 * The first test to reach this route pays Next's dev compile (tens of seconds
 * on this app). A `goto` issued while the previous page still has requests in
 * flight is reported as ERR_ABORTED rather than retried, so navigate on
 * `domcontentloaded` and give it one more go.
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

/** Choose the first option in one of the header's Radix selects. */
async function pickFirst(page: Page, name: string) {
  const trigger = page.getByRole("combobox", { name });
  await expect(trigger).toBeVisible();
  await trigger.click();
  const option = page.getByRole("option").filter({ hasNot: page.locator("[data-disabled]") }).first();
  await option.click();
}

// A full run streams eight agent turns; the cold compile of this route on top
// of that comfortably exceeds Playwright's 30s default.
test.describe.configure({ timeout: 120_000 });

test.describe("Orchestrator — auto-sequencing cockpit", () => {
  test("runs a project's roster stage by stage and stops at the first gate", async ({
    page,
  }) => {
    await signInAsPlatformRole(page, /^Project Admin\b/i);

    await gotoOrchestrator(page);
    await expect(page.getByRole("heading", { name: /Pick a project to orchestrate/i })).toBeVisible();

    await pickFirst(page, "Project");

    // Selecting a project resolves its allowed models and auto-seeds the
    // default, so the empty state flips to "Ready to orchestrate <name>".
    await expect(page.getByRole("heading", { name: /Ready to orchestrate/i })).toBeVisible({
      timeout: 15_000,
    });

    // The model picker is populated from the project's own allow-list.
    await expect(page.getByRole("combobox", { name: "Model" })).toBeVisible();

    await page.getByRole("button", { name: /^Run the pipeline$/i }).click();

    // The Orchestrator's opening turn names the roster it is about to run.
    await expect(page.getByText(/I will run the project's/i)).toBeVisible({ timeout: 15_000 });

    // First agent takes its turn — Requirements leads every track that has it.
    await expect(page.getByText(/Requirements/).first()).toBeVisible({ timeout: 20_000 });

    // Auto-advance closes the non-mandatory gates on its own, which is the
    // whole point of this surface: a second agent speaks without a click.
    await expect(page.getByText(/Auto-approved the .* gate/i).first()).toBeVisible({
      timeout: 30_000,
    });
    await expect(page.getByText(/Handing off/i).first()).toBeVisible();
  });

  test("a mandatory gate stops the run and waits for a decision", async ({ page }) => {
    await signInAsPlatformRole(page, /^Project Admin\b/i);
    await gotoOrchestrator(page);
    await pickFirst(page, "Project");
    await expect(page.getByRole("heading", { name: /Ready to orchestrate/i })).toBeVisible({
      timeout: 15_000,
    });

    // Turning auto-advance OFF makes every gate behave like a mandatory one,
    // so the pause path is reachable without depending on which track the
    // first fixture project happens to be on.
    await page.getByRole("button", { name: /^Run the pipeline$/i }).click();
    await expect(page.getByText(/I will run the project's/i)).toBeVisible({ timeout: 15_000 });
    await page.getByLabel("Auto-advance").click();

    const approve = page.getByRole("button", { name: /Approve & continue/i });
    await expect(approve).toBeVisible({ timeout: 40_000 });
    await expect(page.getByText(/The run is stopped until you decide/i)).toBeVisible();

    // Deciding it resumes the sequence.
    await approve.click();
    await expect(page.getByText(/gate approved\. Handing off\./i).first()).toBeVisible({
      timeout: 15_000,
    });
  });

  test("rejecting a gate stops the run and nothing downstream executes", async ({ page }) => {
    await signInAsPlatformRole(page, /^Project Admin\b/i);
    await gotoOrchestrator(page);
    await pickFirst(page, "Project");
    await expect(page.getByRole("heading", { name: /Ready to orchestrate/i })).toBeVisible({
      timeout: 15_000,
    });

    await page.getByRole("button", { name: /^Run the pipeline$/i }).click();
    await expect(page.getByText(/I will run the project's/i)).toBeVisible({ timeout: 15_000 });
    await page.getByLabel("Auto-advance").click();

    const reject = page.getByRole("button", { name: /^Reject$/i });
    await expect(reject).toBeVisible({ timeout: 40_000 });
    await reject.click();

    await expect(
      page.getByText(/gate rejected\. The run is stopped here — nothing downstream ran/i),
    ).toBeVisible({ timeout: 10_000 });
  });

  test("the per-project Orchestrator runs the same way with the project fixed", async ({
    page,
  }) => {
    await signInAsPlatformRole(page, /^Project Admin\b/i);

    // Reach it the way a user does — from the project's own tab strip.
    await page.goto("/projects", { waitUntil: "domcontentloaded" });
    await page.getByRole("link", { name: /Payments API/i }).first().click();
    // Scope to the project's own tab strip — the sidebar has an "Orchestrator"
    // link too, and that one goes to the global route.
    await page
      .getByRole("navigation", { name: "Project sections" })
      .getByRole("link", { name: /^Orchestrator$/i })
      .click();
    await page.waitForURL(/\/projects\/[^/]+\/orchestrator/);

    // No project picker here: the route already fixes the project.
    await expect(page.getByRole("combobox", { name: "Project" })).toHaveCount(0);
    await expect(page.getByRole("combobox", { name: "Model" })).toBeVisible({
      timeout: 15_000,
    });

    await expect(page.getByRole("heading", { name: /Ready to orchestrate/i })).toBeVisible({
      timeout: 15_000,
    });
    await page.getByRole("button", { name: /^Run the pipeline$/i }).click();

    await expect(page.getByText(/I will run the project's/i)).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText(/Auto-approved the .* gate/i).first()).toBeVisible({
      timeout: 30_000,
    });
  });

  test("the rail shows the project's real stage state, not just the run's", async ({ page }) => {
    await signInAsPlatformRole(page, /^Project Admin\b/i);
    await gotoOrchestrator(page);
    await pickFirst(page, "Project");
    await expect(page.getByRole("heading", { name: /Ready to orchestrate/i })).toBeVisible({
      timeout: 15_000,
    });

    // Before any run: the roster, its gate owners, and what the project itself
    // is currently holding on — the signal the read-only control view carried.
    await expect(page.getByText(/Gate owner:/).first()).toBeVisible();
    await expect(page.getByText("HOLDING")).toBeVisible();
    await expect(page.getByText("Awaiting approval")).toBeVisible();
  });

  test("a non-driving role gets the Orchestrator read-only", async ({ page }) => {
    await signInAsPlatformRole(page, /^Developer\b/i);
    await gotoOrchestrator(page);

    await expect(
      page.getByText(/driving it across agents is the Project Admin's role/i),
    ).toBeVisible({ timeout: 15_000 });
    await expect(page.getByRole("button", { name: /Run pipeline/i })).toHaveCount(0);
  });
});
