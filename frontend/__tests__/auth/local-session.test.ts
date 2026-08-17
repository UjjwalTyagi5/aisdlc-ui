/**
 * Unit tests for buildLocalSession (local email+password auth).
 *
 * Asserts the login response → Session mapping: the tenant, the role derived from
 * the resolved permission set, and the permissions carried verbatim so the BFF
 * minter and hasPermission gate see the real backend vocabulary.
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
  it("maps an org admin (admin:*) to admin role with its tenant", () => {
    const s = buildLocalSession(resp({ permissions: ["admin:*"] }), "ada@acme.test");
    expect(s.role).toBe("admin");
    expect(s.tier).toBe("org");
    expect(s.tenant.id).toBe("00000000-0000-0000-0000-000000000001");
    expect(s.mode).toBe("local");
  });

  it("no longer treats platform:* as admin — the platform tier was removed", () => {
    const s = buildLocalSession(resp({ permissions: ["platform:*"] }), "boss@co.com");
    expect(s.role).toBe("viewer");
    expect(s.permissions).toEqual(["platform:*"]);
  });

  it("gives a freshly registered account no role and no permissions", () => {
    const s = buildLocalSession(resp({ permissions: [] }), "newcomer@acme.test");
    expect(s.role).toBe("viewer");
    expect(s.permissions).toEqual([]);
    // Still attached to the one organization — signup joins it, it just grants nothing.
    expect(s.tenant.id).toBe("00000000-0000-0000-0000-000000000001");
  });

  it("names the tenant from the backend when it sends one", () => {
    expect(buildLocalSession(resp({ tenant_name: "PWC" }), "a@acme.test").tenant.name).toBe(
      "PWC",
    );
    expect(buildLocalSession(resp({}), "a@acme.test").tenant.name).toBe("Organization");
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
