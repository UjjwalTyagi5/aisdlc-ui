import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";

/**
 * A source-level guard, not a render test.
 *
 * The linearity this removes is structural — a rail component, an auto-advance
 * flag, a "run the pipeline" action. A render test would only prove they are not
 * visible in one state; this proves they are gone. The Orchestrator picks any
 * agent at any time (spec D7), so any re-introduction of ordering controls should
 * fail loudly here and be a deliberate decision.
 */
const cockpit = readFileSync(
  join(process.cwd(), "components/orchestrator/cockpit.tsx"),
  "utf8",
);

describe("orchestrator cockpit", () => {
  it.each(["stage-rail", "StageRail", "autoAdvance", "Auto-advance", "Run pipeline"])(
    "no longer references %s",
    (banned) => {
      expect(cockpit).not.toContain(banned);
    },
  );

  it("mounts the artifacts panel", () => {
    expect(cockpit).toContain("components/orchestrator/artifacts-panel");
  });
});
