import { describe, expect, it } from "vitest";
import { existsSync } from "node:fs";
import { join } from "node:path";

/**
 * The Orchestrator used to fake its agents: `script.ts` held hardcoded per-agent
 * prose and `use-orchestrator.ts` revealed it on a timer, so the page looked like
 * it was streaming from a backend that did not exist. Both are gone. This test
 * exists so they cannot quietly come back as a "temporary" stand-in while the real
 * engine is being built.
 */
describe("orchestrator mock engine", () => {
  it.each(["script.ts", "use-orchestrator.ts"])("%s is gone", (file) => {
    expect(existsSync(join(process.cwd(), "lib/orchestrator", file))).toBe(false);
  });
});
