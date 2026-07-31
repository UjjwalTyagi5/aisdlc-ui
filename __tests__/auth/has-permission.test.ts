/**
 * Unit tests for hasPermission + approvePermissionForPhase (REQ-M7-12 frontend
 * half / milestone-7.2 plan 05).
 *
 * Asserts the frontend gate mirrors the backend `has_permission` EXACTLY
 * (agentic_app/shared/authz/permissions.py): denial when the permission is
 * absent, grant when present, the `admin:*` wildcard passes everything, and
 * null/empty sessions fail closed.
 */
import { describe, it, expect } from "vitest";

import { approvePermissionForPhase, hasPermission } from "@/lib/auth/permissions";
import type { Session } from "@/lib/auth/types";

function buildSession(permissions: string[]): Session {
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
  };
}

describe("hasPermission", () => {
  it("returns false when the session lacks the required permission", () => {
    const session = buildSession(["artifact:view", "run:create"]);
    expect(hasPermission(session, "artifact:approve_design")).toBe(false);
  });

  it("returns true when the session holds the required permission", () => {
    const session = buildSession(["artifact:view", "artifact:approve_design"]);
    expect(hasPermission(session, "artifact:approve_design")).toBe(true);
  });

  it("admin:* wildcard grants ANY M7.2 permission, including ones not literally listed", () => {
    const session = buildSession(["admin:*"]);
    expect(hasPermission(session, "artifact:approve_design")).toBe(true);
    expect(hasPermission(session, "artifact:approve_deployment")).toBe(true);
    expect(hasPermission(session, "connector:manage")).toBe(true);
    expect(hasPermission(session, "some:unknown-permission-not-in-vocab")).toBe(
      true,
    );
  });

  it("denies for null session and for an empty-permissions session", () => {
    expect(hasPermission(null, "artifact:view")).toBe(false);
    const empty = buildSession([]);
    expect(hasPermission(empty, "artifact:view")).toBe(false);
  });
});

describe("approvePermissionForPhase", () => {
  it("maps each pipeline phase to its backend approval permission", () => {
    expect(approvePermissionForPhase("requirements")).toBe(
      "artifact:approve_requirements",
    );
    expect(approvePermissionForPhase("design")).toBe("artifact:approve_design");
    expect(approvePermissionForPhase("development")).toBe(
      "artifact:approve_development",
    );
    expect(approvePermissionForPhase("testing")).toBe(
      "artifact:approve_testing",
    );
    expect(approvePermissionForPhase("deployment")).toBe(
      "artifact:approve_deployment",
    );
  });

  it("returns a never-granted safe-deny sentinel for 'review' (no backend approval permission)", () => {
    const sentinel = approvePermissionForPhase("review");
    expect(sentinel).toBe("artifact:approve_review");
    // Fail-closed proof: no non-admin session can ever hold the sentinel.
    const memberSession = buildSession([
      "artifact:view",
      "artifact:approve_requirements",
      "artifact:approve_design",
      "artifact:approve_development",
      "artifact:approve_testing",
      "artifact:approve_deployment",
      "run:create",
      "connector:manage",
    ]);
    expect(hasPermission(memberSession, sentinel)).toBe(false);
    // ...but admin:* still passes it (wildcard semantics preserved).
    expect(hasPermission(buildSession(["admin:*"]), sentinel)).toBe(true);
  });
});
