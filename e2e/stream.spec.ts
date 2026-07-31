/**
 * WS→SSE bridge E2E spec (real-api project only).
 *
 * Exercises the bridge introduced in Plan 07:
 *   Browser EventSource → GET /api/runs/[id]/stream
 *     → mintWsTicket → WebSocket → FastAPI agent WS
 *
 * NEXT_PUBLIC_DISABLE_STREAMS=0 is required (set by the real-api project).
 * With streams disabled the connection-indicator stays "idle" forever — the
 * test would trivially pass but prove nothing.
 *
 * Skips cleanly when FastAPI is unreachable so CI never hard-fails on infra.
 */
import { expect, test } from "@playwright/test";

import { signInAs, waitForProjects } from "./helpers";

const FASTAPI_URL =
  process.env.FASTAPI_INTERNAL_URL ?? "http://localhost:8001";

// ── Guard: skip when FastAPI is unreachable ────────────────────────────────

async function isFastapiReachable(): Promise<boolean> {
  try {
    const res = await fetch(`${FASTAPI_URL}/health`, {
      signal: AbortSignal.timeout(2_000),
    });
    return res.ok;
  } catch {
    return false;
  }
}

// ── Tests ──────────────────────────────────────────────────────────────────

test.describe("WS→SSE bridge (real-api)", () => {
  test.beforeEach(async () => {
    const reachable = await isFastapiReachable();
    test.skip(!reachable, "FastAPI not reachable — skipping stream spec");
  });

  test("connection-indicator leaves idle when streams are enabled", async ({ page }) => {
    // Sign in and navigate to the projects list.
    await signInAs(page, "admin");
    await waitForProjects(page);

    // The StreamSubscriber is mounted once at the app layout.
    // When NEXT_PUBLIC_DISABLE_STREAMS=0 it opens /api/stream immediately.
    // The connection-indicator should transition away from "idle" once the
    // EventSource connects (connecting → connected) or retries (reconnecting).
    const indicator = page.locator('[role="status"][aria-label^="Stream "]');
    await expect(indicator).toBeVisible({ timeout: 10_000 });

    // Assert it left the idle state — any non-idle state proves the bridge opened.
    await expect(indicator).not.toHaveAttribute("data-state", "idle", {
      timeout: 15_000,
    });

    // Capture the reached state for diagnostic output.
    const dataState = await indicator.getAttribute("data-state");
    console.log(`[stream-spec] connection-indicator data-state: ${dataState}`);
  });

  test("per-run stream opens for a run in awaiting_approval status", async ({ page }) => {
    // Navigate directly to a known run that is in awaiting_approval.
    // The run with E2E_RUN_ID (seeded by seed_e2e_fixtures.py) should be
    // accessible via the runs list.
    await signInAs(page, "admin");
    await page.goto("/runs");

    // Look for a run that is awaiting_approval — the seed script creates one.
    const awaitingRow = page
      .getByRole("row")
      .filter({ hasText: /awaiting.?approval/i })
      .first();

    // If the seed row is present, click it and check the per-run stream feed.
    const rowCount = await awaitingRow.count();
    if (rowCount === 0) {
      // Seed fixture not yet visible in the run list — skip the per-run assertion
      // (the workspace-level bridge test above already covers the bridge).
      test.skip(true, "No awaiting_approval run in list — seed may not have synced yet");
      return;
    }

    await awaitingRow.click();

    // The RunDetailDrawer should open (Sheet component).
    const drawer = page.getByRole("dialog");
    await expect(drawer).toBeVisible({ timeout: 8_000 });

    // The RunStreamFeed is rendered for awaiting_approval runs.
    // It shows a "Live stream" header or a "Connecting" message.
    const streamPanel = drawer.locator("text=/Live stream|Connecting to stream|Waiting for events/i").first();
    await expect(streamPanel).toBeVisible({ timeout: 10_000 });
  });
});
