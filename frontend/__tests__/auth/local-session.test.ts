/**
 * Unit tests for buildLocalSession (Phase 3 local email+password auth).
 *
 * Asserts the login response → Session mapping: tier-aware tenant, role derived
 * from the resolved permission set, and the permissions carried verbatim so the
 * BFF minter and hasPermission gate see the real backend vocabulary.
 */
import { describe, it, expect } from "vitest";

import { buildLocalSession, type LoginResponse } from "@/lib/auth/local";

function resp(over: Partial<LoginResponse>): LoginResponse {
  return {
    token: "t",
    tier: "org",
    user_id: "u1",
    tenant_id: "00000000-0000-0000-0000-000000000001",
    permissions: [],
    ...over,
  };
}

describe("buildLocalSession", () => {
  it("maps a platform admin to admin role + tenant-less + platform tier", () => {
    const s = buildLocalSession(
      resp({ tier: "platform", tenant_id: null, permissions: ["platform:*"] }),
      "boss@co.com",
    );
    expect(s.role).toBe("admin");
    expect(s.tier).toBe("platform");
    expect(s.tenant.id).toBe(""); // tenant-less
    expect(s.permissions).toEqual(["platform:*"]);
    expect(s.mode).toBe("local");
  });

  it("maps an org admin (admin:*) to admin role with its tenant", () => {
    const s = buildLocalSession(resp({ permissions: ["admin:*"] }), "ada@acme.test");
    expect(s.role).toBe("admin");
    expect(s.tier).toBe("org");
    expect(s.tenant.id).toBe("00000000-0000-0000-0000-000000000001");
  });

  it("derives member from run:create / approve permissions", () => {
    const s = buildLocalSession(
      resp({ permissions: ["run:create", "artifact:view"] }),
      "dev@acme.test",
    );
    expect(s.role).toBe("member");
  });

  it("derives viewer from a read-only permission set", () => {
    const s = buildLocalSession(resp({ permissions: ["artifact:view"] }), "v@acme.test");
    expect(s.role).toBe("viewer");
  });

  it("computes initials from the email local part", () => {
    expect(buildLocalSession(resp({}), "ada.lovelace@acme.test").user.initials).toBe("AL");
    expect(buildLocalSession(resp({}), "grace@acme.test").user.initials).toBe("GR");
  });
});
