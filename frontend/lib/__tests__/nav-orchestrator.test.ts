import { describe, expect, it } from "vitest";

import { deliverNav, visibleDeliverNav } from "@/lib/nav";
import type { PlatformRole } from "@/lib/roles";

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

  /**
   * The assertion above proves the FIELD is set — it says nothing about
   * whether `visibleTo()` actually reads it. Exercising the real filter
   * (`visibleDeliverNav`, the function the sidebar calls) is what proves a
   * non-admin is actually excluded rather than merely mislabeled.
   */
  it("is admitted for project_admin", () => {
    const visible = visibleDeliverNav([], { role: "project_admin" });
    expect(visible.some((i) => i.segment === "orchestrator")).toBe(true);
  });

  it.each<PlatformRole>(["ba", "developer", "qa", "architect", "org_admin", "bu_admin"])(
    "is excluded for %s",
    (role) => {
      const visible = visibleDeliverNav([], { role });
      expect(visible.some((i) => i.segment === "orchestrator")).toBe(false);
    },
  );
});
