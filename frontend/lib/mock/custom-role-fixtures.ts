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
  /** The owning Business Unit, or null for an org-wide role. Decided by the
   *  route handler from the caller's scope, never taken from the browser. */
  businessUnitId?: string | null;
}): CustomRole {
  const role: CustomRole = {
    id: `role_${nextId++}`,
    name: input.name,
    description: input.description ?? null,
    permissions: input.permissions,
    agentAccess: input.agentAccess,
    scope: input.scope,
    businessUnitId: input.businessUnitId ?? null,
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

// ─── Ownership (who may write which role) ────────────────────────────────────

/** The bit of an access scope this module needs — kept structural so both the
 *  route handlers and the MSW handlers can pass what they already resolved,
 *  without this file importing the scope resolver. */
export interface RoleOwnerScope {
  isOrgWide: boolean;
  managedBusinessUnitIds: string[];
}

/**
 * Which Business Unit a caller's new role belongs to, or a refusal.
 *
 * The server decides ownership; `businessUnitId` arriving from the browser is
 * treated as a request, not a fact. A Business Unit Admin who could name any
 * unit here would be able to plant an assignable role inside someone else's —
 * a quiet way to grant permissions in a unit you do not run.
 *
 *   org-wide caller   may create an org-wide role (null) or one pinned to any
 *                     unit they name.
 *   unit admin        gets their own unit, and is refused if they name another.
 *                     With several administered units the request must say
 *                     which, because guessing would silently pin the role to a
 *                     unit whose people it was never meant for.
 */
export function resolveRoleOwner(
  scope: RoleOwnerScope,
  requested: string | null | undefined,
): { businessUnitId: string | null } | { error: string } {
  if (scope.isOrgWide) return { businessUnitId: requested ?? null };

  const managed = scope.managedBusinessUnitIds;
  if (managed.length === 0) return { error: "You do not administer a business unit." };
  if (requested) {
    return managed.includes(String(requested))
      ? { businessUnitId: String(requested) }
      : { error: "You can only define roles for a business unit you administer." };
  }
  if (managed.length > 1) {
    return { error: "Say which business unit this role belongs to." };
  }
  return { businessUnitId: managed[0]! };
}

/**
 * May this caller change or delete this role?
 *
 * An org-wide role (`businessUnitId: null`) is the Organization Admin's: a unit
 * admin assigns it but must not be able to rewrite what it grants everywhere
 * else. Their own unit's roles are theirs entirely.
 */
export function canWriteCustomRole(scope: RoleOwnerScope, role: CustomRole): boolean {
  if (scope.isOrgWide) return true;
  return role.businessUnitId !== null && scope.managedBusinessUnitIds.includes(role.businessUnitId);
}
