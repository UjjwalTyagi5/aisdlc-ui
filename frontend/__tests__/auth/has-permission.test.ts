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

  it("maps the three gates that used to fall through to the sentinel", () => {
    // These asserted the sentinel, which encoded the belief that `_PHASE_PERMISSION`
    // omitted them. It does not — it maps all eight stages, and the gate handler
    // enforces them. The frontend hiding the approve control for exactly the three
    // gates the backend also refused is what made the hole look like working software.
    //
    // Note the key asymmetry: the frontend Phase is `review`, the backend stage is
    // `code_review`, and the permission string is the backend's.
    expect(approvePermissionForPhase("review")).toBe("artifact:approve_code_review");
    expect(approvePermissionForPhase("security")).toBe("artifact:approve_security");
    expect(approvePermissionForPhase("documentation")).toBe(
      "artifact:approve_documentation",
    );
  });

  it("still returns a never-granted sentinel for phases with no backend gate", () => {
    // `discovery`, `strategy`, `migration_mapping`, `validation` and `data_engineering`
    // genuinely have no entry in `_PHASE_PERMISSION`. Fail-closed is the right answer
    // for them, and pinning it here stops a future edit to the `default` case from
    // silently granting something.
    for (const phase of ["discovery", "strategy", "migration_mapping"]) {
      const sentinel = approvePermissionForPhase(phase);
      expect(sentinel).toBe("artifact:approve_review");
      const memberSession = buildSession([
        "artifact:view",
        "artifact:approve_requirements",
        "artifact:approve_code_review",
        "artifact:approve_security",
        "artifact:approve_documentation",
        "run:create",
      ]);
      expect(hasPermission(memberSession, sentinel)).toBe(false);
      // ...but admin:* still passes it (wildcard semantics preserved).
      expect(hasPermission(buildSession(["admin:*"]), sentinel)).toBe(true);
    }
  });
});
