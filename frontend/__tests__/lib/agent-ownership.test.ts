import { describe, expect, it } from "vitest";
import { AGENT_OWNERSHIP } from "@/lib/roles";

const PORTFOLIO_1 = [
  "requirements", "design", "development", "review",
  "security", "testing", "deployment", "documentation",
] as const;

describe("AGENT_OWNERSHIP matches the PRD's §14.7 table for Portfolio 1", () => {
  it("ba owns Requirements and Documentation, and nothing else", () => {
    expect(AGENT_OWNERSHIP.ba.requirements).toBe("owner");
    expect(AGENT_OWNERSHIP.ba.documentation).toBe("owner");
    for (const phase of ["design", "plan", "development", "review", "security",
                         "testing", "deployment"] as const) {
      expect(AGENT_OWNERSHIP.ba[phase], `ba should not reach ${phase}`).toBe("none");
    }
  });

  it("architect owns Design and Code Review, and nothing else in the pipeline", () => {
    expect(AGENT_OWNERSHIP.architect.design).toBe("owner");
    expect(AGENT_OWNERSHIP.architect.review).toBe("owner");
    for (const phase of ["requirements", "plan", "development", "security",
                         "testing", "deployment", "documentation"] as const) {
      expect(AGENT_OWNERSHIP.architect[phase], `architect should not reach ${phase}`)
        .toBe("none");
    }
  });

  it("developer owns Development only", () => {
    expect(AGENT_OWNERSHIP.developer.development).toBe("owner");
    for (const phase of ["requirements", "design", "plan", "review", "security",
                         "testing", "deployment", "documentation"] as const) {
      expect(AGENT_OWNERSHIP.developer[phase], `developer should not reach ${phase}`)
        .toBe("none");
    }
  });

  it("qa owns Testing only", () => {
    expect(AGENT_OWNERSHIP.qa.testing).toBe("owner");
    expect(AGENT_OWNERSHIP.qa.requirements).toBe("none");
    expect(AGENT_OWNERSHIP.qa.development).toBe("none");
    expect(AGENT_OWNERSHIP.qa.security).toBe("none");
  });

  it("security_engineer owns Security only", () => {
    expect(AGENT_OWNERSHIP.security_engineer.security).toBe("owner");
    expect(AGENT_OWNERSHIP.security_engineer.requirements).toBe("none");
    expect(AGENT_OWNERSHIP.security_engineer.design).toBe("none");
    expect(AGENT_OWNERSHIP.security_engineer.deployment).toBe("none");
    expect(AGENT_OWNERSHIP.security_engineer.documentation).toBe("none");
  });

  it("devops_engineer owns Deployment only", () => {
    expect(AGENT_OWNERSHIP.devops_engineer.deployment).toBe("owner");
    expect(AGENT_OWNERSHIP.devops_engineer.security).toBe("none");
    expect(AGENT_OWNERSHIP.devops_engineer.testing).toBe("none");
    expect(AGENT_OWNERSHIP.devops_engineer.development).toBe("none");
  });

  it("scrum_master (Project Manager) owns Plan only", () => {
    expect(AGENT_OWNERSHIP.scrum_master.plan).toBe("owner");
    expect(AGENT_OWNERSHIP.scrum_master.requirements).toBe("none");
    expect(AGENT_OWNERSHIP.scrum_master.documentation).toBe("none");
  });

  it("no role keeps a `use` tier — reach is ownership now", () => {
    // The softer tier is what let a BA reach all nine. If it reappears anywhere,
    // "which agents does this role own" and "which can it open" diverge again.
    for (const role of Object.keys(AGENT_OWNERSHIP) as Array<keyof typeof AGENT_OWNERSHIP>) {
      for (const [phase, involvement] of Object.entries(AGENT_OWNERSHIP[role])) {
        expect(
          ["owner", "none"],
          `${role}.${phase} is "${involvement}" — expected owner or none`,
        ).toContain(involvement);
      }
    }
  });

  it("project_admin owns every Portfolio-1 agent", () => {
    for (const phase of PORTFOLIO_1) {
      expect(AGENT_OWNERSHIP.project_admin[phase]).toBe("owner");
    }
  });
});
