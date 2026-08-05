import { describe, expect, it } from "vitest";

import {
  canManageIntegration,
  manageDeniedReason,
  onboardsAt,
} from "@/lib/integrations/manage-scope";

const orgAdmin = { role: "org_admin", businessUnitIds: [] };
const buAdmin = { role: "bu_admin", businessUnitIds: ["ws_payments"] };
const projectAdmin = { role: "project_admin", businessUnitIds: ["ws_payments"] };

const orgWide = { scope: "organization" as const, workspaceId: null };
const paymentsOwned = { scope: "business_unit" as const, workspaceId: "ws_payments" };
const lendingOwned = { scope: "business_unit" as const, workspaceId: "ws_lending" };

describe("canManageIntegration", () => {
  it("lets an Organization Admin manage org-wide integrations", () => {
    expect(canManageIntegration(orgAdmin, orgWide)).toBe(true);
  });

  it("stops an Organization Admin managing a unit's own integration", () => {
    // They outrank the person who onboarded it and still do not own it — the
    // whole point of the split.
    expect(canManageIntegration(orgAdmin, paymentsOwned)).toBe(false);
  });

  it("lets a Business Unit Admin manage their own unit's integration", () => {
    expect(canManageIntegration(buAdmin, paymentsOwned)).toBe(true);
  });

  it("stops a Business Unit Admin managing an org-wide integration", () => {
    expect(canManageIntegration(buAdmin, orgWide)).toBe(false);
  });

  it("stops a Business Unit Admin managing a sibling unit's integration", () => {
    expect(canManageIntegration(buAdmin, lendingOwned)).toBe(false);
  });

  it("treats an un-onboarded unit placeholder as manageable by a BU Admin who holds a unit", () => {
    const placeholder = { scope: "business_unit" as const, workspaceId: null };
    expect(canManageIntegration(buAdmin, placeholder)).toBe(true);
    expect(canManageIntegration({ role: "bu_admin", businessUnitIds: [] }, placeholder)).toBe(false);
  });

  it("never lets a Project Admin onboard", () => {
    // A project consumes and configures its own credentials; it does not onboard.
    expect(canManageIntegration(projectAdmin, orgWide)).toBe(false);
    expect(canManageIntegration(projectAdmin, paymentsOwned)).toBe(false);
  });

  it("denies an unknown or absent role", () => {
    expect(canManageIntegration({ role: null, businessUnitIds: [] }, orgWide)).toBe(false);
    expect(canManageIntegration({ role: "developer", businessUnitIds: [] }, orgWide)).toBe(false);
  });
});

describe("onboardsAt agrees with canManageIntegration", () => {
  it("an admin can always manage what they just onboarded", () => {
    for (const viewer of [orgAdmin, buAdmin]) {
      const scope = onboardsAt(viewer.role);
      const created = {
        scope,
        workspaceId: scope === "business_unit" ? (viewer.businessUnitIds[0] ?? null) : null,
      };
      expect(canManageIntegration(viewer, created)).toBe(true);
    }
  });
});

describe("manageDeniedReason", () => {
  it("returns null when management is allowed, so presence of a reason is the gate", () => {
    expect(manageDeniedReason(orgAdmin, orgWide, "Business Unit")).toBeNull();
    expect(manageDeniedReason(buAdmin, paymentsOwned, "Business Unit")).toBeNull();
  });

  it("tells an Org Admin what they DO control instead", () => {
    const reason = manageDeniedReason(orgAdmin, paymentsOwned, "Business Unit");
    expect(reason).toMatch(/its own Admin manages it/i);
    expect(reason).toMatch(/may have it at all/i);
  });

  it("distinguishes inherited from someone else's, for a BU Admin", () => {
    expect(manageDeniedReason(buAdmin, orgWide, "Business Unit")).toMatch(/inherit/i);
    expect(manageDeniedReason(buAdmin, lendingOwned, "Business Unit")).toMatch(/another/i);
  });
});
