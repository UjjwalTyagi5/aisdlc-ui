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

  it("owner for the Developer once development is verified", () => {
    // The Development agent belongs to the Developer now; AGENT_OWNER_ROLE moved
    // with it, so the Architect — which no longer reaches Development — is locked.
    const builtWithDev: readonly (typeof BUILT_AGENTS)[number][] = [...BUILT_AGENTS, "development"];
    expect(tileStateFor("developer", "development", "greenfield", builtWithDev)).toBe("owner");
    expect(tileStateFor("architect", "development", "greenfield", builtWithDev)).toBe("locked");
  });

  it("coming_soon before development is verified, regardless of role", () => {
    const builtWithoutDev: readonly (typeof BUILT_AGENTS)[number][] = BUILT_AGENTS.filter((p) => p !== "development");
    expect(tileStateFor("architect", "development", "greenfield", builtWithoutDev)).toBe("coming_soon");
  });
});
