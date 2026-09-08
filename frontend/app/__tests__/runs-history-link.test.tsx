import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

/**
 * The runs list is the platform-wide run table — pipeline runs, webhook runs and agent
 * runs alike. It used to open a row in the Copilot, which Phase 5 deletes.
 *
 * It now opens the read-only conversation view. History is for reading; continuing a
 * chat belongs to the Orchestrator's own rail, which Phase 5B makes real. Ruling R10
 * kept these links pointing at the Copilot precisely because nothing else could open a
 * past run — that is no longer true.
 *
 * A SOURCE check rather than a render test, deliberately: the failure being prevented
 * is a link to a route that no longer exists, and such a link renders perfectly. It
 * fails when somebody clicks it.
 */
const FILES = [
  "app/(app)/runs/page.tsx",
  "components/app/project-runs-table.tsx",
];

describe("run history", () => {
  it.each(FILES)("%s never links to the retired Copilot page", (file) => {
    expect(readFileSync(file, "utf8")).not.toContain("/copilot?run=");
  });

  it.each(FILES)("%s links to the read-only conversation view", (file) => {
    expect(readFileSync(file, "utf8")).toContain("/conversation");
  });
});
