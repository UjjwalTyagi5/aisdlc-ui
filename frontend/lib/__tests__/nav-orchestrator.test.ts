import { describe, expect, it } from "vitest";

import { deliverNav } from "@/lib/nav";

/**
 * The Orchestrator entry used to be gated on `artifact:view` — which every
 * delivery role holds — because the page was a mock and opening it did nothing.
 * It now reaches all nine agents, so visibility and usability must agree: a tab
 * that opens onto "no access" is a worse experience than no tab.
 */
describe("orchestrator nav entry", () => {
  const entry = deliverNav.find((i) => i.segment === "orchestrator");

  it("exists", () => {
    expect(entry).toBeDefined();
  });

  it("is restricted to project_admin, not to a permission every role holds", () => {
    expect(entry?.requirePermission).toBeUndefined();
    expect(entry?.requirePlatformRole).toEqual(["project_admin"]);
  });
});
