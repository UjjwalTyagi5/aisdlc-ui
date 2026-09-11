import { describe, expect, it } from "vitest";

import {
  BUILT_AGENTS,
  GATE_POLICY,
  PHASE_LABEL,
  builtAgentsForTrack,
  phaseHref,
  phaseRoute,
} from "@/lib/agents";
import { tileStateFor } from "@/lib/agent-access";
import { AGENT_OWNER_ROLE, AGENT_OWNERSHIP } from "@/lib/roles";
import { agentsForTrack, trackHasAgent } from "@/lib/tracks";
import { OrchestratorAgentId } from "@/lib/orchestrator/agents";
import { PHASE_FOR_AGENT, agentLabel } from "@/lib/orchestrator/types";
import { toolStagesForTrack } from "@/components/app/tools-stage-picker";
import { approvePermissionForPhase } from "@/lib/auth/permissions";

/**
 * Track 3 (Code Modernization) Phase 1: its first two agents are real, and they are
 * TRACK 3's — its own migration-intent Requirements agent and Discovery & Assessment —
 * while the other eight of its roster stay "Coming soon" even though Portfolio 1
 * agents with the same names are built.
 */
describe("Track 3 roster", () => {
  it("starts with its own migration-intent Requirements agent, then Discovery", () => {
    const roster = agentsForTrack("modernization");
    expect(roster.slice(0, 2)).toEqual(["requirements_modernization", "discovery"]);
    expect(roster).toHaveLength(10);
    expect(roster).not.toContain("requirements");
  });

  it("never appears on a Greenfield or Enhancement project", () => {
    for (const track of ["greenfield", "enhancement"] as const) {
      expect(trackHasAgent(track, "requirements_modernization")).toBe(false);
      expect(trackHasAgent(track, "discovery")).toBe(false);
    }
  });

  it("builds exactly its first two agents; Portfolio 1's list is unchanged", () => {
    expect(builtAgentsForTrack("modernization")).toEqual(["requirements_modernization", "discovery"]);
    expect(builtAgentsForTrack("greenfield")).toBe(BUILT_AGENTS);
    expect(BUILT_AGENTS).not.toContain("discovery");
  });

  it("routes to its own pages", () => {
    expect(phaseRoute("requirements_modernization")).toBe("requirements-modernization");
    expect(phaseHref("p1", "discovery")).toBe("/projects/p1/discovery");
    expect(PHASE_LABEL.requirements_modernization).toBe("Migration Intent");
  });
});

describe("Track 3 tiles", () => {
  const built = builtAgentsForTrack("modernization");

  it("gives the BA ownership of both agents — the product decision for Track 3", () => {
    expect(tileStateFor("ba", "requirements_modernization", "modernization", built)).toBe("owner");
    expect(tileStateFor("ba", "discovery", "modernization", built)).toBe("owner");
  });

  it("gives the Project Admin both, as the fallback owner on every agent", () => {
    expect(tileStateFor("project_admin", "requirements_modernization", "modernization", built)).toBe("owner");
    expect(tileStateFor("project_admin", "discovery", "modernization", built)).toBe("owner");
  });

  it("locks them for roles that do not own them", () => {
    expect(tileStateFor("architect", "discovery", "modernization", built)).toBe("locked");
    expect(tileStateFor("developer", "requirements_modernization", "modernization", built)).toBe("locked");
  });

  it("shows the rest of Track 3's roster as Coming soon, even for the Project Admin", () => {
    for (const phase of ["design", "strategy", "development", "review", "security", "testing", "deployment", "documentation"] as const) {
      expect(tileStateFor("project_admin", phase, "modernization", built)).toBe("coming_soon");
    }
  });

  it("keeps the ownership tables and gate policy in agreement", () => {
    expect(AGENT_OWNER_ROLE.requirements_modernization).toBe("ba");
    expect(AGENT_OWNER_ROLE.discovery).toBe("ba");
    expect(AGENT_OWNERSHIP.architect.discovery).toBe("none");
    expect(GATE_POLICY.discovery.ownerLabel).toBe(GATE_POLICY.requirements_modernization.ownerLabel);
    expect(approvePermissionForPhase("discovery")).toBe("artifact:approve_discovery");
    expect(approvePermissionForPhase("requirements_modernization")).toBe("artifact:approve_requirements_modernization");
  });
});

describe("Track 3 in the Orchestrator", () => {
  it("accepts both agents on the wire — an unknown id would be dropped by Zod", () => {
    expect(OrchestratorAgentId.parse("discovery")).toBe("discovery");
    expect(OrchestratorAgentId.parse("requirements_modernization")).toBe("requirements_modernization");
  });

  it("labels them the way the rest of the app does", () => {
    expect(PHASE_FOR_AGENT.discovery).toBe("discovery");
    expect(agentLabel("discovery")).toBe("Dependency and Risk");
    expect(agentLabel("requirements_modernization")).toBe("Migration Intent");
  });
});

describe("Track 3 tool wiring", () => {
  it("lists the track's own stages, so Discovery can be given a repository connector", () => {
    const ids = toolStagesForTrack("modernization").map((s) => s.id);
    expect(ids.slice(0, 2)).toEqual(["requirements_modernization", "discovery"]);
    // Portfolio 1's `review` phase is the `code_review` agent id.
    expect(ids).toContain("code_review");
  });

  it("keeps the Greenfield stages exactly as before", () => {
    const ids = toolStagesForTrack("greenfield").map((s) => s.id);
    expect(ids).toEqual([
      "requirements", "design", "plan", "development", "code_review",
      "security", "testing", "deployment", "documentation",
    ]);
  });
});
