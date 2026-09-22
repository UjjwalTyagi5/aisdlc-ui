import { describe, expect, it } from "vitest";
import { tileStateFor } from "@/lib/agent-access";

describe("tileStateFor", () => {
  it("is 'owner' when the role owns the agent and it's built", () => {
    expect(tileStateFor("security_engineer", "security", "greenfield", ["security"])).toBe("owner");
  });

  it("is 'locked' when the role does not own the agent, even though it is built", () => {
    // There is no `use` tier any more — reach IS ownership, so a Developer looking
    // at Security gets the same locked tile as any other agent it does not own.
    expect(tileStateFor("developer", "security", "greenfield", ["security"])).toBe("locked");
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

  // The API's own answer for this viewer (`getMyAgentAccess().reach`) folds in the
  // extra agents granted from the Members page. A QA granted Code Review and Security
  // saw both padlocked because the tile was decided from the static table alone.
  describe("with the API's reach for this viewer", () => {
    const built = ["security", "review", "testing", "development"] as const;
    const reach = { security: "use", review: "use", testing: "owner", development: "none" } as const;

    it("unlocks an extra agent the role's table would lock", () => {
      expect(tileStateFor("qa", "security", "greenfield", built, reach)).toBe("use");
      expect(tileStateFor("qa", "review", "greenfield", built, reach)).toBe("use");
    });

    it("keeps the role's own agent as owner and the rest locked", () => {
      expect(tileStateFor("qa", "testing", "greenfield", built, reach)).toBe("owner");
      expect(tileStateFor("qa", "development", "greenfield", built, reach)).toBe("locked");
    });

    it("falls back to the table for a phase the API did not answer", () => {
      expect(tileStateFor("qa", "security", "greenfield", built, {})).toBe("locked");
      expect(tileStateFor("qa", "testing", "greenfield", built, {})).toBe("owner");
    });

    it("never unlocks what the track has not built", () => {
      expect(tileStateFor("qa", "security", "greenfield", [], reach)).toBe("coming_soon");
    });
  });
});
