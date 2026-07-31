/**
 * Unit tests for the platform-role resolver (`lib/auth/effective-role.ts`).
 *
 * The resolver has two paths: it reads `session.platformRole` when the backend
 * issues it, and otherwise infers the role from the session's permission
 * bundle (sound because a role IS a bundle — PRD §14.10, §14.11).
 *
 * The backend does not issue `platformRole` today, so the inference is what
 * actually runs. These tests pin BOTH paths, which is the point: they prove
 * the authoritative path already works, so the day the backend starts emitting
 * the field the switchover is silent and needs no code change.
 */
import { describe, it, expect } from "vitest";

import {
  dashboardScope,
  effectivePlatformRole,
  effectiveRoleLabel,
  isGovernanceRole,
} from "@/lib/auth/effective-role";
import type { Session } from "@/lib/auth/types";

function buildSession(permissions: string[], platformRole?: string): Session {
  return {
    user: {
      id: "u_test_01",
      name: "Test User",
      email: "test@acme.test",
      initials: "TU",
    },
    tenant: {
      id: "ws_test",
      name: "Test Corp",
      plan: "Enterprise",
    },
    role: "member",
    mode: "mock",
    permissions,
    ...(platformRole === undefined ? {} : { platformRole }),
  };
}

/** Permission bundles that identify a role by inference alone (§14.7). */
const ORG_ADMIN_PERMS = ["admin:*"];
const QA_PERMS = ["artifact:view", "artifact:approve_testing"];

describe("effectivePlatformRole — inferred path (no platformRole issued)", () => {
  it("returns null for a null session", () => {
    expect(effectivePlatformRole(null)).toBeNull();
  });

  it("infers org_admin from the admin:* wildcard", () => {
    expect(effectivePlatformRole(buildSession(ORG_ADMIN_PERMS))).toBe("org_admin");
  });

  it("infers org_admin from settings:manage, which only that role holds (§14.10)", () => {
    expect(effectivePlatformRole(buildSession(["settings:manage"]))).toBe("org_admin");
  });

  it("infers bu_admin from unit governance without org-wide settings", () => {
    expect(effectivePlatformRole(buildSession(["role:manage"]))).toBe("bu_admin");
  });

  it("infers project_admin from member:manage, unique to it in the delivery tier (§14.1)", () => {
    expect(effectivePlatformRole(buildSession(["member:manage"]))).toBe("project_admin");
  });

  it("infers a contributor from the gate it owns (§14.7)", () => {
    expect(effectivePlatformRole(buildSession(QA_PERMS))).toBe("qa");
    expect(effectivePlatformRole(buildSession(["artifact:approve_design"]))).toBe("architect");
    expect(effectivePlatformRole(buildSession(["artifact:approve_requirements"]))).toBe("ba");
    expect(effectivePlatformRole(buildSession(["artifact:approve_deployment"]))).toBe(
      "devops_engineer",
    );
  });

  it("checks security_engineer before other contributors — it is the one with standing audit + trace (§15.9)", () => {
    const session = buildSession(["audit:view", "trace:view", "artifact:approve_testing"]);
    expect(effectivePlatformRole(session)).toBe("security_engineer");
  });

  it("infers developer from run:create — builds but never approves (§15.7)", () => {
    expect(effectivePlatformRole(buildSession(["artifact:view", "run:create"]))).toBe("developer");
  });

  it("infers custom for a bundle holding artifact:view and nothing role-identifying (§14.9)", () => {
    expect(effectivePlatformRole(buildSession(["artifact:view"]))).toBe("custom");
  });

  it("returns null for an empty permission bundle", () => {
    expect(effectivePlatformRole(buildSession([]))).toBeNull();
  });
});

describe("effectivePlatformRole — authoritative path (backend issues platformRole)", () => {
  it("returns the issued role", () => {
    expect(effectivePlatformRole(buildSession(["artifact:view"], "data_engineer"))).toBe(
      "data_engineer",
    );
  });

  it("prefers the issued role over what the permissions would have inferred", () => {
    // admin:* alone infers org_admin; the explicit value must win.
    const session = buildSession(ORG_ADMIN_PERMS, "qa");
    expect(effectivePlatformRole(session)).toBe("qa");
  });

  it("falls back to inference when the issued role is not in the catalogue", () => {
    // A backend shipping a role the UI has no ROLE_META entry for must not
    // escape the resolver — it would throw at every downstream label lookup.
    const session = buildSession(QA_PERMS, "product_manager");
    expect(effectivePlatformRole(session)).toBe("qa");
  });

  it("falls back to inference for an empty issued role", () => {
    expect(effectivePlatformRole(buildSession(QA_PERMS, ""))).toBe("qa");
  });
});

describe("downstream helpers honour the issued role", () => {
  it("effectiveRoleLabel uses the catalogue label", () => {
    expect(effectiveRoleLabel(buildSession(ORG_ADMIN_PERMS, "qa"))).toBe("QA / Tester");
    expect(effectiveRoleLabel(null)).toBeNull();
  });

  it("isGovernanceRole follows the issued role, not the permissions", () => {
    // Permissions say org_admin (governance); the issued role says QA (delivery).
    expect(isGovernanceRole(buildSession(ORG_ADMIN_PERMS, "qa"))).toBe(false);
    expect(isGovernanceRole(buildSession(QA_PERMS, "org_admin"))).toBe(true);
  });

  it("dashboardScope follows the issued role (§36)", () => {
    expect(dashboardScope(buildSession(QA_PERMS, "org_admin"))).toBe("organization");
    expect(dashboardScope(buildSession(QA_PERMS, "bu_admin"))).toBe("business_unit");
    expect(dashboardScope(buildSession(ORG_ADMIN_PERMS, "developer"))).toBe("project");
    expect(dashboardScope(null)).toBe("project");
  });
});
