/**
 * Nothing navigates to the Copilot, because the Copilot no longer exists.
 *
 * FOUND BY AUDITING THE REQUIREMENTS DOC AGAINST THE CODE. `orchestrator_instruction.md`
 * §1.1-1.2: the Copilot is removed and "Run agent" leads to the Orchestrator. Phase 5A
 * deleted `app/(app)/projects/[id]/copilot/` and both old engines — but
 * `components/runs/run-trigger-dialog.tsx:61` still ran
 *
 *     router.push(`/projects/${projectId}/copilot?run=${runId}`)
 *
 * reachable from the project Workstreams page. Starting a workstream succeeded, showed
 * "Run started", and then dropped the user on a 404.
 *
 * It also created a run nobody used. The Orchestrator opens its own run when a session
 * starts, so the row this dialog pre-created was orphaned the moment it was written —
 * and its model picker chose an offering the Orchestrator then re-chose for itself.
 *
 * So the dialog is gone rather than repaired: with the model choice and the run
 * creation both belonging to the Orchestrator, what was left was a button that
 * navigates, which is `RunAgentButton`.
 *
 * This test is a REPO-WIDE GREP, not a component render. The bug was one string in one
 * file that no test rendered, and a component test would only have covered whichever
 * component someone remembered to write it for.
 */
import fs from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

const ROOT = path.resolve(__dirname, "../../..");
const SEARCH_DIRS = ["app", "components", "lib"];
const EXTENSIONS = new Set([".ts", ".tsx"]);

/** Every source file under the searched directories, tests excluded. */
function sourceFiles(dir: string, out: string[] = []): string[] {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      if (entry.name === "node_modules" || entry.name === "__tests__") continue;
      sourceFiles(full, out);
    } else if (EXTENSIONS.has(path.extname(entry.name))) {
      out.push(full);
    }
  }
  return out;
}

const FILES = SEARCH_DIRS.flatMap((d) => {
  const full = path.join(ROOT, d);
  return fs.existsSync(full) ? sourceFiles(full) : [];
});

describe("the deleted Copilot route", () => {
  it("has source files to search, so a passing grep means something", () => {
    // Without this, a broken ROOT would make every assertion below vacuously true.
    expect(FILES.length).toBeGreaterThan(100);
  });

  it("is not the route the Copilot page used to live at", () => {
    // Guards the test itself: if the page ever came back, these assertions would be
    // asserting the wrong thing and should fail loudly rather than keep passing.
    expect(fs.existsSync(path.join(ROOT, "app/(app)/projects/[id]/copilot"))).toBe(
      false,
    );
  });

  it("is navigated to by nothing", () => {
    // The deleted PAGE, `/projects/<id>/copilot`. Scoped to that shape on purpose:
    // storage keys and react-query cache keys legitimately carry the word "copilot"
    // and are not routes, and `/runs/<id>/copilot/advance` is a backend path with its
    // own separate problem (see the next test).
    const offenders = FILES.filter((file) =>
      /\/projects\/[^"'`]*\/copilot/.test(fs.readFileSync(file, "utf8")),
    ).map((f) => path.relative(ROOT, f));

    expect(offenders).toEqual([]);
  });

  it("is not the only thing Phase 5A left pointing at a deleted endpoint", () => {
    // NOT FIXED HERE, and recorded so it is not mistaken for fixed. `advanceCopilotRun`
    // (lib/api/runs.ts) posts to `/runs/<id>/copilot/advance`, which the backend no
    // longer defines — `backend/tests/orchestrator2/test_old_engines_are_gone.py`
    // asserts its absence outright. Three live components still call it:
    // approval-gate-row, run-detail-drawer and run-conversation.
    //
    // Its replacement is `POST /runs/<id>/approvals`, whose own docstring says it
    // "mirrors copilot_advance" — but mapping one onto the other is an approvals
    // question, not a routing one, so it is left for a deliberate change rather than
    // smuggled into this one. This assertion documents the state and will fail the
    // moment someone fixes it, which is the prompt to delete this test.
    const client = fs.readFileSync(path.join(ROOT, "lib/api/runs.ts"), "utf8");
    expect(client).toContain("/copilot/advance");
  });

  it("is not reachable from the Workstreams page", () => {
    const page = fs.readFileSync(
      path.join(ROOT, "app/(app)/projects/[id]/workstreams/page.tsx"),
      "utf8",
    );
    expect(page).not.toContain("RunTriggerDialog");
    expect(page).toContain("RunAgentButton");
  });

  it("left no dialog behind that creates a run the Orchestrator will not use", () => {
    expect(fs.existsSync(path.join(ROOT, "components/runs/run-trigger-dialog.tsx")))
      .toBe(false);
  });
});
