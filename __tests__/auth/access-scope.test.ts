import { describe, expect, it } from "vitest";

import { buildMockSessionForPlatformRole } from "@/lib/auth/mock";
import { resolveSessionScope, sessionIdentityId } from "@/lib/auth/access-scope";
import {
  canManageBusinessUnit,
  canReadBusinessUnit,
  canReadGovernanceApproval,
  canReadProject,
  filterByProject,
} from "@/lib/mock/access-scope";
import { GATES } from "@/lib/mock/approval-fixtures";
import { TRACES } from "@/lib/mock/trace-fixtures";
import { AUDIT_EVENTS, PROJECTS } from "@/mocks/fixtures";
import type { PlatformRole } from "@/lib/roles";

/**
 * The scope boundary, pinned. These assertions are the whole point of the RBAC
 * work: a Business Unit Admin must not reach a sibling unit, and a Project Admin
 * must not reach an unassigned project. Both are invisible defects — the UI looks
 * perfectly correct while showing data it shouldn't — so they need tests rather
 * than a visual check.
 */

const scopeFor = (role: PlatformRole) =>
  resolveSessionScope(buildMockSessionForPlatformRole(role));

describe("resolveSessionScope", () => {
  it("binds each mock persona to a seeded identity", () => {
    // Without this the whole model is vacuous: an unbound persona has no unit to
    // be scoped to, so "only your unit" and "everything" look identical.
    expect(sessionIdentityId(buildMockSessionForPlatformRole("bu_admin"))).toBe("idn_noah");
    expect(sessionIdentityId(buildMockSessionForPlatformRole("project_admin"))).toBe("idn_priya");
  });

  it("gives the Organization Admin the whole tenant", () => {
    const scope = scopeFor("org_admin");
    expect(scope.isOrgWide).toBe(true);
    expect(scope.level).toBe("organization");
    expect(scope.projectIds).toHaveLength(PROJECTS.length);
  });

  describe("Business Unit Admin", () => {
    const scope = scopeFor("bu_admin");

    it("manages exactly its own unit", () => {
      expect(scope.isOrgWide).toBe(false);
      expect(scope.level).toBe("business_unit");
      expect(scope.managedBusinessUnitIds).toEqual(["ws_platform"]);
    });

    it("cannot read a sibling unit", () => {
      expect(canReadBusinessUnit(scope, "ws_platform")).toBe(true);
      expect(canReadBusinessUnit(scope, "ws_payments")).toBe(false);
      expect(canReadBusinessUnit(scope, "ws_lending")).toBe(false);
    });

    it("reaches its own unit's projects without being a member of each", () => {
      // recon-bots sits in ws_platform and Noah holds no project binding on it —
      // governance over a unit implies visibility of its projects.
      expect(canReadProject(scope, "recon-bots")).toBe(true);
      expect(canReadProject(scope, "payments-api")).toBe(false);
      expect(canReadProject(scope, "mobile-onboarding")).toBe(false);
    });
  });

  describe("Project Admin", () => {
    const scope = scopeFor("project_admin");

    it("sees only the project it administers", () => {
      // Priya is project_admin on payments-api and `ba` on mobile-onboarding.
      // Acting AS Project Admin must not silently confer the BA binding's reach.
      expect(scope.managedProjectIds).toEqual(["payments-api"]);
      expect(canReadProject(scope, "payments-api")).toBe(true);
      expect(canReadProject(scope, "mobile-onboarding")).toBe(false);
    });

    it("reads the parent unit for context but cannot manage it", () => {
      expect(canReadBusinessUnit(scope, "ws_payments")).toBe(true);
      expect(canManageBusinessUnit(scope, "ws_payments")).toBe(false);
    });

    it("does not administer any Business Unit", () => {
      expect(scope.managedBusinessUnitIds).toEqual([]);
      expect(scope.level).toBe("project");
    });
  });

  it("switches reach when the same person acts as a different role", () => {
    // Priya again, acting as BA: the project_admin binding must not follow her.
    const asBa = scopeFor("ba");
    expect(canReadProject(asBa, "mobile-onboarding")).toBe(true);
    expect(asBa.managedProjectIds).toEqual([]);
  });

  it("fails closed for an unresolvable delivery-tier session", () => {
    const scope = resolveSessionScope({
      ...buildMockSessionForPlatformRole("developer"),
      identityId: undefined,
      user: { id: "u_ghost", name: "Ghost", email: "ghost@nowhere.test", initials: "G" },
    });
    expect(scope.projectIds).toEqual([]);
    expect(scope.businessUnitIds).toEqual([]);
  });
});

describe("canReadGovernanceApproval", () => {
  it("hides a parent unit's governance queue from a Project Admin", () => {
    // Priya administers payments-api and can READ ws_payments for context. That
    // read must not extend to the unit's budget negotiations.
    const scope = scopeFor("project_admin");
    expect(canReadBusinessUnit(scope, "ws_payments")).toBe(true);
    expect(canReadGovernanceApproval(scope, "ws_payments", null)).toBe(false);
  });

  it("still shows a project-scoped approval on a project they administer", () => {
    const scope = scopeFor("project_admin");
    expect(canReadGovernanceApproval(scope, "ws_payments", "payments-api")).toBe(true);
  });

  it("shows a Business Unit Admin only their own unit's queue", () => {
    const scope = scopeFor("bu_admin");
    expect(canReadGovernanceApproval(scope, "ws_platform", null)).toBe(true);
    expect(canReadGovernanceApproval(scope, "ws_lending", null)).toBe(false);
  });
});

describe("fixture coverage", () => {
  /**
   * Scope filtering makes empty fixtures indistinguishable from a broken filter,
   * so every project a persona can reach needs at least one trace and one audit
   * row. These two assertions are what caught the Business Unit Admin's blank
   * Traces tab.
   */
  it("gives every active project at least one trace", () => {
    const covered = new Set(TRACES.map((t) => t.projectId));
    for (const id of ["mobile-onboarding", "payments-api", "core-ledger", "recon-bots"]) {
      expect(covered.has(id as (typeof TRACES)[number]["projectId"])).toBe(true);
    }
  });

  it("gives a single-unit Business Unit Admin non-empty audit and trace sets", () => {
    const scope = scopeFor("bu_admin");
    expect(filterByProject(scope, TRACES, (t) => t.projectId).length).toBeGreaterThan(0);
    expect(filterByProject(scope, AUDIT_EVENTS, (e) => e.projectId).length).toBeGreaterThan(0);
  });
});

describe("filterByProject", () => {
  it("drops approval gates outside the viewer's projects", () => {
    const scope = scopeFor("project_admin");
    const visible = filterByProject(scope, GATES, (g) => g.projectId);
    expect(visible.length).toBeGreaterThan(0);
    expect(visible.every((g) => g.projectId === "payments-api")).toBe(true);
    expect(visible.length).toBeLessThan(GATES.length);
  });

  it("passes everything through for an org-wide viewer", () => {
    const scope = scopeFor("org_admin");
    expect(filterByProject(scope, GATES, (g) => g.projectId)).toHaveLength(GATES.length);
  });
});
