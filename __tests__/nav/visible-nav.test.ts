import { describe, expect, it } from "vitest";

import { mainNav, visibleNav } from "@/lib/nav";
import { ROLE_PERMISSIONS } from "@/lib/auth/role-permissions";

describe("visibleNav", () => {
  it("admin:* sees every item", () => {
    const v = visibleNav(["admin:*"]);
    expect(v.map((i) => i.href)).toEqual(
      mainNav.filter((i) => !i.hiddenInSidebar).map((i) => i.href),
    );
  });
  it("stakeholder (artifact:view) sees only ungated items, not Access/Cost/Audit/Roles", () => {
    const hrefs = visibleNav(["artifact:view"]).map((i) => i.href);
    expect(hrefs).not.toContain("/admin/access");
    expect(hrefs).not.toContain("/admin/roles");
    expect(hrefs).not.toContain("/cost");
    expect(hrefs).not.toContain("/audit");
  });
  it("delivery_lead sees Access + Cost but not Roles or Settings", () => {
    const hrefs = visibleNav([
      "member:manage",
      "cost:view",
      "run:view",
      "artifact:view",
      "connector:view",
    ]).map((i) => i.href);
    expect(hrefs).toContain("/admin/access");
    expect(hrefs).toContain("/cost");
    expect(hrefs).not.toContain("/admin/roles");
    expect(hrefs).not.toContain("/settings");
  });
});

/**
 * The role- and scope-aware half of the filter. Permissions alone cannot express
 * either of these two rules, which is why NavContext exists.
 */
describe("visibleNav — role and scope", () => {
  const hrefsFor = (perms: readonly string[], ctx?: Parameters<typeof visibleNav>[1]) =>
    visibleNav([...perms], ctx).map((i) => i.href);

  it("keeps Agent Studio for the governance tier", () => {
    // It was hidden from both, on the reading that a role with no agent access
    // has no business in a prompt editor. That was right about RUNNING an agent
    // and wrong about this page: it is where each tier publishes the defaults
    // the tiers beneath it inherit, and Organization and Business Unit are the
    // two tiers these roles OWN (AGENT_DEFAULT_OWNER_ROLE). Hiding it left both
    // ownerless — no one could set an org-wide default at all.
    for (const role of ["org_admin", "bu_admin"] as const) {
      expect(hrefsFor(ROLE_PERMISSIONS[role], { role })).toContain("/agent-studio");
    }
  });

  it("keeps Agent Studio for delivery roles", () => {
    expect(hrefsFor(ROLE_PERMISSIONS.project_admin, { role: "project_admin" })).toContain(
      "/agent-studio",
    );
    expect(hrefsFor(ROLE_PERMISSIONS.developer, { role: "developer" })).toContain(
      "/agent-studio",
    );
  });

  it("still hides Orchestrator from the governance tier", () => {
    // The distinction Agent Studio's un-hiding turns on: Orchestrator IS
    // running agents, which neither admin tier does (PRD §14.8). If these two
    // ever agree again, one of them has drifted.
    for (const role of ["org_admin", "bu_admin"] as const) {
      expect(hrefsFor(ROLE_PERMISSIONS[role], { role })).not.toContain("/orchestrator");
    }
    expect(hrefsFor(ROLE_PERMISSIONS.project_admin, { role: "project_admin" })).toContain(
      "/orchestrator",
    );
  });

  it("hides Business Units from someone who administers none", () => {
    // A Project Admin holds no workspace:manage, so the permission gate already
    // covers them; a custom role that DID hold it but administered no unit is
    // the case requireScope exists for.
    const hrefs = hrefsFor(["workspace:manage"], {
      role: "custom",
      isOrgWide: false,
      managedBusinessUnitIds: [],
    });
    expect(hrefs).not.toContain("/workspaces");
  });

  it("shows Business Units to a single-unit Business Unit Admin", () => {
    const hrefs = hrefsFor(ROLE_PERMISSIONS.bu_admin, {
      role: "bu_admin",
      isOrgWide: false,
      managedBusinessUnitIds: ["ws_platform"],
    });
    expect(hrefs).toContain("/workspaces");
  });

  it("does not collapse the menu while scope is still resolving", () => {
    // Undefined scope means "the request hasn't landed", which must not read as
    // "administers nothing" — a menu that vanishes for a frame is a worse bug
    // than one entry that appears a moment early.
    const hrefs = hrefsFor(ROLE_PERMISSIONS.bu_admin, {
      role: "bu_admin",
      isOrgWide: undefined,
      managedBusinessUnitIds: undefined,
    });
    expect(hrefs).toContain("/workspaces");
  });

  it("degrades to permission-only filtering with no context at all", () => {
    expect(hrefsFor(["admin:*"])).toEqual(hrefsFor(["admin:*"], {}));
  });
});
