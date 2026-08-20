import { describe, expect, it } from "vitest";
import { AGENT_OWNERSHIP } from "@/lib/roles";

const PORTFOLIO_1 = [
  "requirements", "design", "development", "review",
  "security", "testing", "deployment", "documentation",
] as const;

describe("AGENT_OWNERSHIP matches the PRD's §14.7 table for Portfolio 1", () => {
  it("ba owns requirements and uses every other Portfolio-1 agent", () => {
    const row = Object.fromEntries(PORTFOLIO_1.map((p) => [p, AGENT_OWNERSHIP.ba[p]]));
    expect(row).toEqual({
      requirements: "primary", design: "use", development: "use", review: "use",
      security: "use", testing: "use", deployment: "use", documentation: "use",
    });
  });

  it("architect owns design, development, and review; uses the rest", () => {
    const row = Object.fromEntries(PORTFOLIO_1.map((p) => [p, AGENT_OWNERSHIP.architect[p]]));
    expect(row).toEqual({
      requirements: "use", design: "primary", development: "primary", review: "primary",
      security: "use", testing: "use", deployment: "use", documentation: "use",
    });
  });

  it("developer reaches requirements, security, and testing at use tier", () => {
    expect(AGENT_OWNERSHIP.developer.requirements).toBe("use");
    expect(AGENT_OWNERSHIP.developer.security).toBe("use");
    expect(AGENT_OWNERSHIP.developer.testing).toBe("use");
    expect(AGENT_OWNERSHIP.developer.deployment).toBe("none");
  });

  it("security_engineer no longer has default reach to documentation", () => {
    expect(AGENT_OWNERSHIP.security_engineer.documentation).toBe("none");
  });

  it("security_engineer reaches requirements, design, and deployment at use tier", () => {
    expect(AGENT_OWNERSHIP.security_engineer.requirements).toBe("use");
    expect(AGENT_OWNERSHIP.security_engineer.design).toBe("use");
    expect(AGENT_OWNERSHIP.security_engineer.deployment).toBe("use");
  });

  it("data_engineer no longer has default reach to development", () => {
    expect(AGENT_OWNERSHIP.data_engineer.development).toBe("none");
  });

  it("data_engineer reaches requirements, design, security, and testing at use tier", () => {
    expect(AGENT_OWNERSHIP.data_engineer.requirements).toBe("use");
    expect(AGENT_OWNERSHIP.data_engineer.design).toBe("use");
    expect(AGENT_OWNERSHIP.data_engineer.security).toBe("use");
    expect(AGENT_OWNERSHIP.data_engineer.testing).toBe("use");
  });

  it("devops_engineer has no default reach to requirements, design, or review", () => {
    expect(AGENT_OWNERSHIP.devops_engineer.requirements).toBe("none");
    expect(AGENT_OWNERSHIP.devops_engineer.design).toBe("none");
    expect(AGENT_OWNERSHIP.devops_engineer.review).toBe("none");
  });

  it("devops_engineer reaches security and testing at use tier", () => {
    expect(AGENT_OWNERSHIP.devops_engineer.security).toBe("use");
    expect(AGENT_OWNERSHIP.devops_engineer.testing).toBe("use");
  });

  it("qa reaches requirements and security at use tier", () => {
    expect(AGENT_OWNERSHIP.qa.requirements).toBe("use");
    expect(AGENT_OWNERSHIP.qa.security).toBe("use");
  });

  it("project_admin owns every Portfolio-1 agent", () => {
    for (const phase of PORTFOLIO_1) {
      expect(AGENT_OWNERSHIP.project_admin[phase]).toBe("owner");
    }
  });
});
