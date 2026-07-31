import { test, expect } from "@playwright/test";
import { signInAs } from "./helpers";

// M5 manual UI verification: SLA countdown renders on an awaiting-approval run,
// and Approve triggers the signal path + advances. Runs on the mock (chromium)
// project — MSW intercepts POST /runs/{id}/signals/{name}.
test("M5 HITL: SLA countdown + Approve on run detail drawer", async ({ page }) => {
  await signInAs(page, "admin");

  await page.goto("/runs");
  // Wait for the runs table body to populate (MSW fixtures).
  const firstRow = page.locator("tbody tr").first();
  await expect(firstRow).toBeVisible({ timeout: 30_000 });
  await page.screenshot({ path: "test-results/m5-00-runs-list.png", fullPage: true });

  // Open an awaiting-approval run if one is listed, else the first row.
  const awaitingRow = page.locator("tbody tr", { hasText: /awaiting|waiting/i }).first();
  const target = (await awaitingRow.count()) > 0 ? awaitingRow : firstRow;
  await target.click();
  await expect(page.getByRole("heading", { name: /Phase Pipeline/i })).toBeVisible({
    timeout: 15_000,
  });

  // Evidence 1: drawer with phase pipeline + (for awaiting_approval) SLA countdown.
  await page.screenshot({ path: "test-results/m5-01-drawer.png", fullPage: true });

  // Approval card present.
  const approval = page.getByLabel("Approval");
  await expect(approval).toBeVisible();

  // SLA countdown: SlaCountdown renders a Clock icon + a time string. Capture the
  // pipeline region specifically so the countdown is visible in the frame.
  await page
    .getByRole("heading", { name: /Phase Pipeline/i })
    .locator("xpath=..")
    .screenshot({ path: "test-results/m5-02-pipeline-countdown.png" });

  const approveBtn = page.getByRole("button", { name: /approve/i }).first();
  await expect(approveBtn).toBeVisible();

  // Evidence 2: the approval card before action.
  await approval.screenshot({ path: "test-results/m5-03-approval-card.png" });

  // Act: approve -> sendApprovalSignal -> POST /runs/{id}/signals/{name} (MSW).
  await approveBtn.click();

  // Observe the resulting state (pending -> decided / advanced).
  await page.waitForTimeout(2_000);
  await page.screenshot({ path: "test-results/m5-04-after-approve.png", fullPage: true });
});
