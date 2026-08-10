/**
 * The Organization Admin's edits to a BUILT-IN role's permissions.
 *
 * WHY AN OVERRIDE LAYER RATHER THAN EDITING THE TABLE. `ROLE_PERMISSIONS` is
 * the platform's shipped answer — what "Developer" means everywhere, in the
 * docs and in every other tenant. Mutating it in place would leave no way to
 * say what changed, and no way back. Storing the delta keeps both: the
 * effective set is what the app enforces, and the default is still there to
 * diff against and reset to.
 *
 * TWO THINGS ARE NOT EDITABLE, deliberately:
 *
 *   `org_admin` — holds `admin:*`, the wildcard every permission check falls
 *   back to. Editing it could lock the last administrator out of the screen
 *   that would let them undo it.
 *
 *   `custom` — a placeholder, not a role anyone holds. Its permissions are
 *   composed per instance in the custom-role builder.
 *
 * Plain data + functions, server-safe, shared by the Next route handlers and
 * mocks/handlers.ts ([[msw-dual-runtime-mutation-rule]]).
 */
import { ROLE_PERMISSIONS } from "@/lib/auth/role-permissions";
import { ROLE_ORDER, type PlatformRole } from "@/lib/roles";

/** Roles whose permission set is fixed. See the docblock. */
export const UNEDITABLE_ROLES = new Set<PlatformRole>(["org_admin", "custom"]);

/** role → the permission ids it holds, when an admin has changed them. */
const OVERRIDES = new Map<PlatformRole, string[]>();

export interface RolePermissionRow {
  role: PlatformRole;
  /** What the platform ships with. */
  defaults: string[];
  /** What it holds now — the override if there is one, else the defaults. */
  effective: string[];
  /** True when an admin has changed it. Drives the "Modified" marker and the
   *  reset control; a diff the reader cannot see is a diff they cannot undo. */
  overridden: boolean;
  editable: boolean;
}

export function listRolePermissions(): RolePermissionRow[] {
  return ROLE_ORDER.map((role) => {
    const defaults = [...(ROLE_PERMISSIONS[role] ?? [])];
    const override = OVERRIDES.get(role);
    return {
      role,
      defaults,
      effective: override ? [...override] : defaults,
      overridden: override !== undefined,
      editable: !UNEDITABLE_ROLES.has(role),
    };
  });
}

/**
 * The permissions one role actually holds — what `hasPermission` should be
 * asked about, rather than the shipped constant.
 */
export function effectiveRolePermissions(role: PlatformRole): readonly string[] {
  return OVERRIDES.get(role) ?? ROLE_PERMISSIONS[role] ?? [];
}

/**
 * Replace a role's permissions.
 *
 * Setting them back to exactly the defaults DELETES the override rather than
 * storing an identical copy — otherwise the role would read as "Modified"
 * forever, having been edited back to where it started.
 */
export function setRolePermissions(role: PlatformRole, permissions: string[]): RolePermissionRow {
  if (UNEDITABLE_ROLES.has(role)) {
    throw new Error(`${role} permissions are fixed and cannot be changed.`);
  }
  const next = [...new Set(permissions)].sort();
  const defaults = [...(ROLE_PERMISSIONS[role] ?? [])].sort();

  if (next.length === defaults.length && next.every((p, i) => p === defaults[i])) {
    OVERRIDES.delete(role);
  } else {
    OVERRIDES.set(role, next);
  }
  return listRolePermissions().find((r) => r.role === role)!;
}

/** Put a role back to what the platform ships with. */
export function resetRolePermissions(role: PlatformRole): RolePermissionRow {
  OVERRIDES.delete(role);
  return listRolePermissions().find((r) => r.role === role)!;
}
