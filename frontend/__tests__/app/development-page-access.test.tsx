import { describe, it, expect } from "vitest";
import { tileStateFor } from "@/lib/agent-access";
import { BUILT_AGENTS } from "@/lib/agents";

describe("Development page access gate", () => {
  it("locked when the role has no reach and development is verified", () => {
    // Once Task 9 adds "development" to BUILT_AGENTS, a role with no reach
    // (e.g. data_engineer, per AGENT_DEFAULT_REACH["development"]) must see
    // "locked", not the file browser.
    const builtWithDev: readonly (typeof BUILT_AGENTS)[number][] = [...BUILT_AGENTS, "development"];
    expect(tileStateFor("data_engineer", "development", "greenfield", builtWithDev)).toBe("locked");
  });

  it("owner for the Architect once development is verified", () => {
    const builtWithDev: readonly (typeof BUILT_AGENTS)[number][] = [...BUILT_AGENTS, "development"];
    expect(tileStateFor("architect", "development", "greenfield", builtWithDev)).toBe("owner");
  });

  it("coming_soon before development is verified, regardless of role", () => {
    expect(tileStateFor("architect", "development", "greenfield", BUILT_AGENTS)).toBe("coming_soon");
  });
});
