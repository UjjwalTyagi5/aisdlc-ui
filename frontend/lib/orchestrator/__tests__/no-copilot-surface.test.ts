import { existsSync } from "node:fs";

import { describe, expect, it } from "vitest";

/**
 * The Copilot is gone.
 *
 * Phase 1 unlinked it but deliberately left it reachable: it was the only surface
 * actually running agents, and deleting it then would have left the product with no
 * working orchestration for the length of the backend work — immediately visible in a
 * demo. The sequencing note in the spec says exactly that.
 *
 * That reason has expired. The Orchestrator runs all nine agents (Phase 2), routes
 * between them from the conversation (Phase 3), persists Deliverables (Phase 4), and
 * keeps its own openable history (Phase 5B).
 */
const GONE = [
  "app/(app)/projects/[id]/copilot",
  "components/copilot",
  "lib/copilot",
  "app/api/copilot",
  "app/api/runs/[id]/copilot",
];

describe("the Copilot surface", () => {
  it.each(GONE)("%s is deleted", (path) => {
    expect(existsSync(path)).toBe(false);
  });
});
