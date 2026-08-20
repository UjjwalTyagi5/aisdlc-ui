import { describe, expect, it } from "vitest";
import { tileStateFor } from "@/lib/agent-access";

describe("tileStateFor", () => {
  it("is 'owner' when the role owns the agent and it's built", () => {
    expect(tileStateFor("security_engineer", "security", "greenfield", ["security"])).toBe("owner");
  });

  it("is 'use' when the role reaches but doesn't own the agent, and it's built", () => {
    expect(tileStateFor("developer", "security", "greenfield", ["security"])).toBe("use");
  });

  it("is 'locked' when the role has no reach, and the agent is built", () => {
    expect(tileStateFor("devops_engineer", "requirements", "greenfield", ["requirements"])).toBe("locked");
  });

  it("is 'coming_soon' when the agent isn't in the built list, regardless of role", () => {
    expect(tileStateFor("security_engineer", "security", "greenfield", [])).toBe("coming_soon");
  });

  it("is 'coming_soon' for every agent on a portfolio with nothing built yet", () => {
    expect(tileStateFor("architect", "discovery", "modernization", [])).toBe("coming_soon");
  });
});
