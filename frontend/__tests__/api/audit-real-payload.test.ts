import { describe, expect, it } from "vitest";
import { AuditEvent } from "@/lib/schemas";

// A verbatim row from GET /audit on the live backend, 2026-09-13.
const REAL_ROW = {
  id: "791ff0cf-b94b-487f-a880-6f3c31d8a50c",
  tenantId: "ac360b60-5779-4eb9-8de2-b739d11228ef",
  projectId: null,
  action: "rbac.role.granted",
  actor: { id: "system", name: "system" },
  resource: { type: "role_binding", id: "d1d96c2b-85e3-41b3-8ac1-fe1fc6e9f28f", name: null },
  at: "2026-09-13T14:49:23.707793+00:00",
  detail: { role: "org_admin", tier: "governance", scope_id: "ac360b60-5779-4eb9-8de2-b739d11228ef" },
  ip: null,
};

describe("AuditEvent against what the backend actually sends", () => {
  it("accepts a real row", () => {
    const r = AuditEvent.safeParse(REAL_ROW);
    expect(r.success, JSON.stringify(r.success ? {} : r.error.issues, null, 2)).toBe(true);
  });

  it("accepts an action outside the known vocabulary", () => {
    expect(AuditEvent.safeParse({ ...REAL_ROW, action: "something.brand.new" }).success).toBe(true);
  });

  it("still rejects a genuinely malformed row", () => {
    const { id: _drop, ...noId } = REAL_ROW;
    expect(AuditEvent.safeParse(noId).success).toBe(false);
  });
});
