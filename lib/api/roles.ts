/**
 * Roles API client — custom-role CRUD + default-role catalog.
 * Calls BFF proxies (/api/admin/custom-roles, /api/admin/roles).
 */
import { z } from "zod";

import { api } from "./client";
import { InvolvementLevel } from "@/lib/schemas/agent-access";
import { Phase } from "@/lib/schemas/enums";

/** Which assignment dropdowns this custom role is offered in — mirrors
 *  lib/scope.ts's tiers, restricted to the ones a custom role can actually
 *  be assigned at (never "workstream"). */
export const CustomRoleScope = z.enum(["organization", "business_unit", "project"]);
export type CustomRoleScope = z.infer<typeof CustomRoleScope>;

const CustomRole = z.object({
  id: z.string(),
  name: z.string(),
  description: z.string().nullable(),
  permissions: z.array(z.string()),
  /** This role's own per-phase agent access — its baseline, same shape as a
   *  built-in role's row in lib/roles.ts::AGENT_OWNERSHIP. Unset phases
   *  default to "none". A project can still override it further via
   *  lib/schemas/agent-access.ts::AgentAccessOverride. */
  agentAccess: z.record(Phase, InvolvementLevel).optional(),
  scope: CustomRoleScope,
  /**
   * The Business Unit that OWNS this role, or null for an org-wide one.
   *
   * Ownership, not merely a filter. A Business Unit Admin composes roles for
   * their own unit's people — "Junior Dev, Payments' version" — and those must
   * neither show up in another unit's assignment dropdown nor be editable from
   * it. Null means the Organization Admin defined it once for everyone, which
   * every unit may assign and only the Org Admin may change.
   */
  businessUnitId: z.string().nullable().default(null),
});
export type CustomRole = z.infer<typeof CustomRole>;

const DefaultRole = z.object({
  name: z.string(),
  label: z.string(),
  description: z.string(),
  permissions: z.array(z.string()),
});
export type DefaultRole = z.infer<typeof DefaultRole>;

export function listCustomRoles() {
  return api("/admin/custom-roles", { schema: z.array(CustomRole) });
}

export function createCustomRole(input: {
  name: string;
  description?: string;
  permissions: string[];
  agentAccess?: Partial<Record<Phase, InvolvementLevel>>;
  scope: CustomRoleScope;
  /** Ignored for a Business Unit Admin — the server pins the role to the unit
   *  they administer rather than trusting the caller with ownership. */
  businessUnitId?: string | null;
}) {
  return api("/admin/custom-roles", { method: "POST", body: input, schema: CustomRole });
}

export function updateCustomRole(
  id: string,
  patch: Partial<{
    name: string;
    description: string | null;
    permissions: string[];
    agentAccess: Partial<Record<Phase, InvolvementLevel>>;
    scope: CustomRoleScope;
  }>,
) {
  return api(`/admin/custom-roles/${id}`, { method: "PATCH", body: patch, schema: CustomRole });
}

export function deleteCustomRole(id: string) {
  return api(`/admin/custom-roles/${id}`, { method: "DELETE", schema: z.unknown() });
}

export function listDefaultRoles() {
  return api("/admin/roles", { schema: z.array(DefaultRole) });
}
