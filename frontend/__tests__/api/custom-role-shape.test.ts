import { describe, expect, it } from "vitest";

import { toCustomRole, type BackendCustomRole } from "@/app/api/admin/custom-roles/route";
import { CustomRoleScope } from "@/lib/api/roles";
import { z } from "zod";
import { InvolvementLevel } from "@/lib/schemas/agent-access";
import { Phase } from "@/lib/schemas/enums";

/**
 * The client validates every custom-role response against this shape. FastAPI speaks
 * `scopeKind`/`scopeId`; the client requires `scope`/`businessUnitId`. The collection
 * route translated, the [id] route proxied PATCH raw — so an edit wrote successfully
 * and then threw "response did not match schema" on the way back, which reads to the
 * user as the save having failed.
 *
 * Redeclared here rather than imported because `CustomRole` is not exported from
 * lib/api/roles.ts; keeping it in step is the point of the test.
 */
const CustomRole = z.object({
  id: z.string(),
  name: z.string(),
  description: z.string().nullable(),
  permissions: z.array(z.string()),
  agentAccess: z.record(Phase, InvolvementLevel).optional(),
  scope: CustomRoleScope,
  businessUnitId: z.string().nullable().default(null),
});

const orgRow: BackendCustomRole = {
  id: "3f6cd7e3-b3c8-47ab-8306-bb12ab2d7c55",
  name: "Reviewer",
  description: null,
  permissions: ["artifact:view"],
  scopeKind: "organization",
  scopeId: "11111111-1111-1111-1111-111111111111",
  createdBy: "someone@example.com",
  agentAccess: null,
};

describe("toCustomRole", () => {
  it("produces a body the client schema accepts", () => {
    expect(CustomRole.safeParse(toCustomRole(orgRow)).success).toBe(true);
  });

  it("renames scopeKind/scopeId to scope/businessUnitId", () => {
    const out = toCustomRole(orgRow);
    expect(out.scope).toBe("organization");
    // An org-wide role is owned by no unit, so it must not carry the tenant id here.
    expect(out.businessUnitId).toBeNull();
    expect("scopeKind" in out).toBe(false);
  });

  it("carries the owning unit for a unit-scoped role", () => {
    const out = toCustomRole({
      ...orgRow,
      scopeKind: "business_unit",
      scopeId: "22222222-2222-2222-2222-222222222222",
    });
    expect(out.scope).toBe("business_unit");
    expect(out.businessUnitId).toBe("22222222-2222-2222-2222-222222222222");
  });

  it("passes agent access straight through", () => {
    const out = toCustomRole({
      ...orgRow,
      agentAccess: { strategy: "primary", development: "build" },
    });
    expect(out.agentAccess).toEqual({ strategy: "primary", development: "build" });
    expect(CustomRole.safeParse(out).success).toBe(true);
  });

  it("maps a null agentAccess to undefined, not null", () => {
    // The schema field is `.optional()`, not `.nullable()` — null would fail it,
    // which is how a role with no agent access set could break its own edit screen.
    expect(toCustomRole(orgRow).agentAccess).toBeUndefined();
  });
});
