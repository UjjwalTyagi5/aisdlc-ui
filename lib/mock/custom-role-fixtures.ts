/**
 * Dummy custom-role store for Roles & Access — an in-memory array (mirrors
 * the mutable-WORKSPACES pattern in workspace-fixtures.ts), so roles created
 * in a session persist for the life of the dev process even though there is
 * no database. None exist by default, which is the correct empty state for a
 * fresh install. Plain data + functions, server-safe (imported by the
 * app/api/admin/custom-roles route handlers). This is the DUMMY-DATA source;
 * the backend roles service replaces the route-handler bodies, not these
 * shapes.
 */
import type { CustomRole, CustomRoleScope } from "@/lib/api/roles";
import type { InvolvementLevel } from "@/lib/schemas/agent-access";
import type { Phase } from "@/lib/schemas/enums";

let nextId = 1;
const CUSTOM_ROLES: CustomRole[] = [];

export function listCustomRoles(): CustomRole[] {
  return CUSTOM_ROLES;
}

export function getCustomRole(id: string): CustomRole | undefined {
  return CUSTOM_ROLES.find((r) => r.id === id);
}

export function createCustomRole(input: {
  name: string;
  description?: string;
  permissions: string[];
  agentAccess?: Partial<Record<Phase, InvolvementLevel>>;
  scope: CustomRoleScope;
}): CustomRole {
  const role: CustomRole = {
    id: `role_${nextId++}`,
    name: input.name,
    description: input.description ?? null,
    permissions: input.permissions,
    agentAccess: input.agentAccess,
    scope: input.scope,
  };
  CUSTOM_ROLES.push(role);
  return role;
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
): CustomRole | undefined {
  const role = CUSTOM_ROLES.find((r) => r.id === id);
  if (!role) return undefined;
  Object.assign(role, patch);
  return role;
}

export function deleteCustomRole(id: string): boolean {
  const i = CUSTOM_ROLES.findIndex((r) => r.id === id);
  if (i === -1) return false;
  CUSTOM_ROLES.splice(i, 1);
  return true;
}
