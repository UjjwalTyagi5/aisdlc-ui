import { describe, expect, it } from "vitest";

import { canUseOrchestrator } from "@/lib/orchestrator/access";
import { ROLE_ORDER } from "@/lib/roles";

describe("canUseOrchestrator", () => {
  it("admits project_admin", () => {
    expect(canUseOrchestrator("project_admin")).toBe(true);
  });

  it("admits nobody else — including the governance tier", () => {
    for (const role of ROLE_ORDER) {
      if (role === "project_admin") continue;
      expect(canUseOrchestrator(role)).toBe(false);
    }
  });

  it("treats a missing role as no access", () => {
    expect(canUseOrchestrator(null)).toBe(false);
    expect(canUseOrchestrator(undefined)).toBe(false);
  });
});
