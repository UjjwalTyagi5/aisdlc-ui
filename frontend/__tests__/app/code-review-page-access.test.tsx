import { describe, it, expect } from "vitest";
import { tileStateFor } from "@/lib/agent-access";
import { BUILT_AGENTS } from "@/lib/agents";

describe("Code Review page access gate", () => {
  it("is on BUILT_AGENTS — the tile is clickable, not 'Coming soon'", () => {
    expect(BUILT_AGENTS).toContain("review");
  });

  it("owner for the Architect, who owns Code Review's gate (AGENT_OWNER_ROLE.review)", () => {
    expect(tileStateFor("architect", "review", "greenfield", BUILT_AGENTS)).toBe("owner");
  });

  it("owner for the Project Admin, the fallback owner on every agent", () => {
    expect(tileStateFor("project_admin", "review", "greenfield", BUILT_AGENTS)).toBe("owner");
  });

  it("locked for QA — AGENT_OWNERSHIP.qa has no review entry, PRD §14.7 marks it none", () => {
    expect(tileStateFor("qa", "review", "greenfield", BUILT_AGENTS)).toBe("locked");
  });

  it("locked for the Data Engineer and the DevOps Engineer — also none per the PRD matrix", () => {
    expect(tileStateFor("data_engineer", "review", "greenfield", BUILT_AGENTS)).toBe("locked");
    expect(tileStateFor("devops_engineer", "review", "greenfield", BUILT_AGENTS)).toBe("locked");
  });

  it("use (not owner) for the Developer, who requests review but never approves it", () => {
    expect(tileStateFor("developer", "review", "greenfield", BUILT_AGENTS)).toBe("use");
  });

  it("would fall back to coming_soon if review were ever pulled off BUILT_AGENTS", () => {
    const withoutReview = BUILT_AGENTS.filter((p) => p !== "review");
    expect(tileStateFor("architect", "review", "greenfield", withoutReview)).toBe("coming_soon");
  });
});
