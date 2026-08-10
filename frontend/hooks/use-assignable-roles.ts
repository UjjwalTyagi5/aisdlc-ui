"use client";

/**
 * Assignable-role options for the app's role-assignment dropdowns — merges
 * the built-in platform roles appropriate to a scope with any custom role
 * scoped to it (lib/api/roles.ts::CustomRole, created on Roles & Access →
 * Custom roles). Before this, a created custom role was fully orphaned: it
 * existed only on its own management screen and could never actually be
 * assigned to anyone, because every dropdown hardcoded a `PlatformRole[]`.
 *
 * Business-Unit-scoped dropdowns (workspace members, Users page invite) and
 * project-scoped dropdowns (project members, project creation contributors)
 * previously each redefined their own filtered role list independently —
 * consolidated here as the single source for both.
 */
import { useQuery } from "@tanstack/react-query";

import { listCustomRoles, type CustomRole, type CustomRoleScope } from "@/lib/api/roles";
import { ROLE_META, ROLE_ORDER, type PlatformRole } from "@/lib/roles";

export interface AssignableRole {
  value: string;
  label: string;
  description?: string;
  isCustom?: boolean;
}

/**
 * The delivery roles a Business Unit Admin may hand out inside their unit.
 *
 * THREE ROLES ARE ABSENT, each for its own reason:
 *
 *   org_admin    appointed org-wide, and not by someone a tier below them.
 *   bu_admin     who RUNS a unit is the Organization Admin's decision, made
 *                from the appointment dialog on Users. A unit admin who could
 *                grant it from here would appoint their own successor — or a
 *                second admin beside themselves — with nobody above the unit
 *                involved in either.
 *   contributor  the ABSENCE of a unit role. Offering it back would let an
 *                admin answer "what does this person do" with "nothing yet",
 *                which is the state they were asked to resolve.
 *
 * `custom` is absent too, but it is a placeholder rather than a role; actual
 * custom roles are merged in separately.
 */
export const BUSINESS_UNIT_ASSIGNABLE_BUILTIN_ROLES: readonly PlatformRole[] = ROLE_ORDER.filter(
  (r) => ROLE_META[r].tier === "delivery" && r !== "custom" && r !== "contributor",
);

/** Contributor roles assignable at the project level — never
 *  project_admin/bu_admin/org_admin. Used by project creation's contributor
 *  picker and the project Members page. */
export const PROJECT_CONTRIBUTOR_ROLES: readonly PlatformRole[] = ROLE_ORDER.filter(
  (r) => ROLE_META[r].scope === "project" && r !== "project_admin",
);

export function toRoleOption(role: PlatformRole): AssignableRole {
  return { value: role, label: ROLE_META[role].label, description: ROLE_META[role].oneLiner };
}

export function toCustomRoleOption(role: CustomRole): AssignableRole {
  return { value: role.id, label: role.name, description: role.description ?? undefined, isCustom: true };
}

/** Custom roles scoped to `scope`, as ready-to-render options. */
export function useCustomRolesForScope(scope: CustomRoleScope): AssignableRole[] {
  const q = useQuery({ queryKey: ["custom-roles"], queryFn: listCustomRoles, staleTime: 60_000 });
  return (q.data ?? []).filter((r) => r.scope === scope).map(toCustomRoleOption);
}

/** The full assignable-role list for a Business-Unit- or project-scoped
 *  dropdown: built-in roles for that scope, plus any custom role scoped to
 *  it. */
export function useAssignableRoles(scope: "business_unit" | "project"): AssignableRole[] {
  const custom = useCustomRolesForScope(scope);
  const builtins = scope === "business_unit" ? BUSINESS_UNIT_ASSIGNABLE_BUILTIN_ROLES : PROJECT_CONTRIBUTOR_ROLES;
  return [...builtins.map(toRoleOption), ...custom];
}

/**
 * The roles assignable to someone inside ONE Business Unit: the built-in unit
 * roles, plus the custom roles that unit owns, plus the org-wide custom roles
 * every unit may use.
 *
 * The narrowing matters. `useAssignableRoles("business_unit")` offers every
 * business-unit-scoped custom role in the organisation, which was harmless
 * while only the Org Admin created them and wrong the moment each unit composes
 * its own: Payments' "Junior Dev" carries Payments' agent access and has no
 * meaning in Lending.
 */
export function useAssignableRolesForBusinessUnit(
  businessUnitId: string | null | undefined,
): AssignableRole[] {
  const all = useAllCustomRoles();
  const custom = all
    .filter(
      (r) =>
        r.scope === "business_unit" &&
        (r.businessUnitId === null ||
          (businessUnitId != null && r.businessUnitId === String(businessUnitId))),
    )
    .map(toCustomRoleOption);
  return [...BUSINESS_UNIT_ASSIGNABLE_BUILTIN_ROLES.map(toRoleOption), ...custom];
}

/** All custom roles, for components (like the Users page) that need to
 *  resolve a role name to a label without being scoped to just one dropdown. */
export function useAllCustomRoles(): CustomRole[] {
  const q = useQuery({ queryKey: ["custom-roles"], queryFn: listCustomRoles, staleTime: 60_000 });
  return q.data ?? [];
}

/** Resolve a stored role string (a `PlatformRole`, or a custom role's id) to
 *  its human label — falls back to a slug replace only for a truly unknown
 *  value. */
export function resolveRoleLabel(name: string, customRoles: CustomRole[]): string {
  if (name in ROLE_META) return ROLE_META[name as PlatformRole].shortLabel;
  const custom = customRoles.find((r) => r.id === name);
  if (custom) return custom.name;
  return name.replace(/_/g, " ");
}
