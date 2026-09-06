import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

/**
 * The Orchestrator must not import from `lib/copilot`.
 *
 * Phase 5 deletes that directory. Until this test existed, seven Orchestrator modules
 * imported from it — so "delete the Copilot" would have broken the surface that
 * replaces it, and only at render time: a missing module still typechecks until
 * something actually resolves it, so `vitest` and `tsc` both stay green.
 *
 * This is the guard that makes the deletion safe rather than hopeful.
 */
function walk(dir: string): string[] {
  return readdirSync(dir).flatMap((entry) => {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) return walk(full);
    return /\.tsx?$/.test(entry) ? [full] : [];
  });
}

describe("the Orchestrator owns its own vocabulary", () => {
  it("imports nothing from lib/copilot", () => {
    const offenders = [...walk("lib/orchestrator"), ...walk("components/orchestrator")]
      .map((f) => f.replace(/\\/g, "/"))
      // This file names the forbidden prefix in order to search for it, so scanning
      // itself would make the guard permanently red and eventually be deleted.
      .filter((f) => !f.endsWith("no-copilot-imports.test.ts"))
      .filter((f) => readFileSync(f, "utf8").includes("@/lib/copilot/"));
    expect(offenders).toEqual([]);
  });
});
