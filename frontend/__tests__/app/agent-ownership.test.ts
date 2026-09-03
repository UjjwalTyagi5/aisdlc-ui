import { describe, expect, it } from "vitest";

import { PHASE_LABEL, PHASE_ORDER } from "@/lib/agents";
import {
  AGENT_OWNER_ROLE,
  AGENT_OWNERSHIP,
  ROLE_ORDER,
  type PlatformRole,
} from "@/lib/roles";
import { roleAgentSplit, roleReachesAgent } from "@/lib/agent-access";
import { canCreateProject, hasPermission } from "@/lib/auth/permissions";

/**
 * Roles that hold no agent access by design.
 *
 * `contributor` is delivery-tier and still belongs here: it is the placeholder
 * a person carries between being onboarded and being given a role by their
 * Business Unit Admin. Reaching an agent with it would mean onboarding alone
 * granted agent access, which is the thing the two-step flow exists to prevent.
 */
const NO_AGENT_ROLES: PlatformRole[] = ["org_admin", "bu_admin", "contributor", "custom"];
const DELIVERY_ROLES = ROLE_ORDER.filter(
  (r) => !NO_AGENT_ROLES.includes(r) && r !== "project_admin",
);

describe("AGENT_OWNERSHIP invariants", () => {
  it("every agent's owner can reach the agent it owns", () => {
    // The one that would break approvals silently: a gate routes to
    // AGENT_OWNER_ROLE, so an owner with `none` leaves a sign-off addressed to
    // someone who cannot open the page it is on.
    for (const phase of PHASE_ORDER) {
      const owner = AGENT_OWNER_ROLE[phase];
      expect(
        roleReachesAgent(owner, phase),
        `${PHASE_LABEL[phase]} is owned by ${owner}, who cannot reach it`,
      ).toBe(true);
    }
  });

  it("every agent has exactly one owner, and that owner is a real role", () => {
    for (const phase of PHASE_ORDER) {
      expect(ROLE_ORDER).toContain(AGENT_OWNER_ROLE[phase]);
    }
  });

  it("Documentation stays reachable by every delivery role except security_engineer", () => {
    // Its acceptance is automatic and every role writes into it — the one
    // genuinely shared surface, and the easiest to lose to an over-zealous
    // narrowing. Security Engineer is excepted by the PRD (§14.7).
    for (const role of DELIVERY_ROLES) {
      if (role === "security_engineer") continue;
      expect(roleReachesAgent(role, "documentation"), `${role} lost Documentation`).toBe(
        true,
      );
    }
  });

  it("no delivery role reaches every agent — least privilege actually bites", () => {
    // Guards the regression this table was rewritten to fix: roles composed
    // from ALL_USE reached all thirteen, so "locked" was always empty and
    // every access affordance built on it was dead UI.
    for (const role of DELIVERY_ROLES) {
      const { locked } = roleAgentSplit(role);
      expect(locked.length, `${role} reaches every agent`).toBeGreaterThan(0);
    }
  });

  it("holds the roles that were specified explicitly", () => {
    const reach = (role: PlatformRole) => roleAgentSplit(role).reachable.sort();

    expect(reach("ba")).toEqual(
      ["requirements", "design", "plan", "development", "review", "security", "testing", "deployment", "documentation"].sort(),
    );
    expect(reach("developer")).toEqual(
      ["requirements", "plan", "development", "review", "security", "testing", "documentation"].sort(),
    );
    expect(reach("qa")).toEqual(
      ["requirements", "plan", "development", "security", "testing", "documentation", "validation"].sort(),
    );
    expect(reach("devops_engineer")).toEqual(
      ["development", "security", "testing", "deployment", "documentation"].sort(),
    );
    // Architect additionally holds `review` and the three modernization-track
    // agents BECAUSE IT OWNS THEM — see the note in lib/roles.ts.
    expect(reach("architect")).toEqual(
      [
        "requirements",
        "design",
        "plan",
        "development",
        "review",
        "security",
        "testing",
        "deployment",
        "documentation",
        "discovery",
        "strategy",
        "migration_mapping",
      ].sort(),
    );
  });

  it("keeps the governance tier and custom at no access", () => {
    for (const role of NO_AGENT_ROLES) {
      expect(roleAgentSplit(role).reachable).toEqual([]);
    }
  });

  it("keeps Project Admin as the fallback approver on everything", () => {
    for (const phase of PHASE_ORDER) {
      expect(AGENT_OWNERSHIP.project_admin[phase]).toBe("owner");
    }
  });
});

describe("canCreateProject", () => {
  it("allows the two roles that own a project's home", () => {
    expect(canCreateProject("bu_admin")).toBe(true);
    expect(canCreateProject("project_admin")).toBe(true);
  });

  it("refuses the Organization Admin despite admin:*", () => {
    // The reason this is a role check and not a permission check: `admin:*`
    // satisfies `project:create`, which org_admin was never granted. A
    // project is created inside a Business Unit, against its budget and
    // members — the Org Admin creates the units, not what runs in them.
    expect(canCreateProject("org_admin")).toBe(false);
    expect(hasPermission({ permissions: ["admin:*"] } as never, "project:create")).toBe(true);
  });

  it("refuses delivery contributors and an unresolved role", () => {
    expect(canCreateProject("developer")).toBe(false);
    expect(canCreateProject("ba")).toBe(false);
    expect(canCreateProject(null)).toBe(false);
  });
});
