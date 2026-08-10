/**
 * Access scope — WHICH Business Units and projects a person may see.
 *
 * This is the missing half of the RBAC model. `lib/auth/permissions.ts` answers
 * "may this viewer perform this KIND of action" (`member:manage`,
 * `artifact:approve_design`, …). It has never answered "on WHICH Business Unit
 * or project", so every list endpoint returned the whole tenant and a Business
 * Unit Admin could read a sibling unit's projects, approvals, spend and audit
 * trail. Permission answers the verb; this module answers the noun.
 *
 * A role is a binding of (person, scope, role) — never a property of the person
 * (PRD §33.1, and [[per-scope-tier-separation]]). So the resolver's input is an
 * identity and its output is a set of bindings, from which the visible id sets
 * are derived:
 *
 *   Organization Admin   organization-scoped binding → the whole tenant
 *   Business Unit Admin  one bu_admin binding → that unit + its projects
 *   Project Admin        project bindings → those projects, and read-only
 *                        context on the parent unit they sit in
 *   Contributors         same as Project Admin, minus the admin affordances
 *
 * DUMMY-DATA SEAM. The joins below read the two membership stores
 * (`workspace-fixtures.ts` MEMBERSHIPS and `project-membership-fixtures.ts`)
 * plus `mocks/fixtures.ts` PROJECTS for the project → parent-unit edge. A real
 * backend issues the resolved scope on the session and this module is deleted;
 * the SHAPES here are the contract. Nothing in this file is an enforcement
 * boundary — it drives what the UI fetches and shows, and the server stays
 * authoritative (see `lib/auth/access-scope.ts`).
 */
import { PROJECTS } from "@/mocks/fixtures";
import { ROLE_META, type PlatformRole } from "@/lib/roles";

import { listMembershipsForIdentity, listWorkspaces } from "./workspace-fixtures";
import { listProjectMembershipsForIdentity } from "./project-membership-fixtures";

// The mock persona map lives in lib/auth so the session builder can read it
// without pulling this module's fixture graph into its bundle.
export { personaIdentityFor } from "@/lib/auth/persona-identity";

export type ScopeKind = "organization" | "business_unit" | "project";

/** One (scope, role) binding a person holds. */
export interface ScopeBinding {
  kind: ScopeKind;
  /** Workspace id for a Business Unit, project id for a project. */
  scopeId: string;
  scopeName: string;
  role: string;
  /** For a project binding, the Business Unit it belongs to (if resolvable). */
  parentId: string | null;
  parentName: string | null;
  status: "active" | "invited" | "deactivated";
}

/**
 * The resolved answer to "what may this viewer see".
 *
 * `businessUnitIds` is deliberately WIDER than `managedBusinessUnitIds`: a
 * Project Admin on a Lending project needs Lending's name, budget cap and
 * connector list to make sense of their own project, but must not be able to
 * administer the unit or see its other projects. Two sets, two meanings —
 * collapsing them into one is how a read-only context leak becomes a write.
 */
export interface AccessScope {
  /** Widest scope the viewer holds a binding at — drives dashboard shape. */
  level: ScopeKind;
  /** True for an organization-scoped role: no filtering applies at all. */
  isOrgWide: boolean;
  /** Business Units the viewer may READ (own units + parents of own projects). */
  businessUnitIds: string[];
  /** Business Units the viewer ADMINISTERS (a `bu_admin` binding). */
  managedBusinessUnitIds: string[];
  /** Projects the viewer may READ. */
  projectIds: string[];
  /** Projects the viewer ADMINISTERS (a `project_admin` binding). */
  managedProjectIds: string[];
  /** The bindings that produced the sets above. */
  actingBindings: ScopeBinding[];
  /** Every binding the person holds, for the "my access" / directory views. */
  allBindings: ScopeBinding[];
  /** Resolved identity, or null when the session maps to no seeded person. */
  identityId: string | null;
}

/** An org-wide scope — used for Organization Admin and as the unauth fallback. */
export function orgWideScope(identityId: string | null, bindings: ScopeBinding[] = []): AccessScope {
  const units = listWorkspaces();
  return {
    level: "organization",
    isOrgWide: true,
    businessUnitIds: units.map((w) => String(w.id)),
    managedBusinessUnitIds: units.map((w) => String(w.id)),
    projectIds: PROJECTS.map((p) => String(p.id)),
    managedProjectIds: PROJECTS.map((p) => String(p.id)),
    actingBindings: bindings,
    allBindings: bindings,
    identityId,
  };
}

/** A scope that can see nothing — the honest answer for an unbound person. */
export function emptyScope(identityId: string | null, bindings: ScopeBinding[] = []): AccessScope {
  return {
    level: "project",
    isOrgWide: false,
    businessUnitIds: [],
    managedBusinessUnitIds: [],
    projectIds: [],
    managedProjectIds: [],
    actingBindings: [],
    allBindings: bindings,
    identityId,
  };
}

// ─── Binding lookup ───────────────────────────────────────────────────────────

/** Every binding an identity holds, across both membership stores. */
export function listBindingsForIdentity(identityId: string): ScopeBinding[] {
  const units = listWorkspaces();
  const unitName = (id: string) =>
    units.find((w) => String(w.id) === id)?.displayName ?? id;

  const unitBindings: ScopeBinding[] = listMembershipsForIdentity(identityId).map((m) => ({
    kind: "business_unit" as const,
    scopeId: String(m.workspaceId),
    scopeName: unitName(String(m.workspaceId)),
    role: m.role,
    parentId: null,
    parentName: null,
    status: m.status,
  }));

  const projectBindings: ScopeBinding[] = listProjectMembershipsForIdentity(identityId).map((m) => {
    const project = PROJECTS.find((p) => String(p.id) === m.projectId);
    const parentId = project?.workspaceId ? String(project.workspaceId) : null;
    return {
      kind: "project" as const,
      scopeId: m.projectId,
      scopeName: project?.name ?? m.projectId,
      role: m.role,
      parentId,
      parentName: parentId ? unitName(parentId) : null,
      status: m.status,
    };
  });

  return [...unitBindings, ...projectBindings];
}

// ─── Resolution ───────────────────────────────────────────────────────────────

function isPlatformRole(value: string): value is PlatformRole {
  return Object.prototype.hasOwnProperty.call(ROLE_META, value);
}

/**
 * Resolve the scope an identity acts within, as a given role.
 *
 * The `actingRole` filter is what keeps a multi-role person coherent. Priya is
 * `project_admin` on one project and `ba` on another; signing in as Project
 * Admin must not silently confer BA reach, and signing in as BA must not confer
 * project administration. So the bindings that MATCH the acting role are the
 * ones that grant scope — and if the identity holds no binding with that role
 * (a custom bundle, or a stale role name), every binding counts instead, since
 * showing a real member nothing at all would be a worse lie than showing them
 * everything they belong to.
 */
export function resolveAccessScope(
  identityId: string | null,
  actingRole: PlatformRole | null,
): AccessScope {
  if (!identityId) {
    // No resolvable person. An org-scoped role still governs everything (its
    // authority does not come from a membership row); anyone else sees nothing.
    return actingRole && ROLE_META[actingRole].scope === "organization"
      ? orgWideScope(null)
      : emptyScope(null);
  }

  const allBindings = listBindingsForIdentity(identityId);

  // Organization-scoped roles are not bounded by membership rows.
  if (actingRole && ROLE_META[actingRole].scope === "organization") {
    return orgWideScope(identityId, allBindings);
  }
  // …nor is an identity holding an organization-scoped role in any unit.
  const holdsOrgRole = allBindings.some(
    (b) => isPlatformRole(b.role) && ROLE_META[b.role].scope === "organization",
  );
  if (holdsOrgRole && !actingRole) {
    return orgWideScope(identityId, allBindings);
  }

  const matching = actingRole ? allBindings.filter((b) => b.role === actingRole) : [];
  const acting = matching.length > 0 ? matching : allBindings;
  if (acting.length === 0) return emptyScope(identityId, allBindings);

  const managedBusinessUnitIds = unique(
    acting.filter((b) => b.kind === "business_unit" && b.role === "bu_admin").map((b) => b.scopeId),
  );
  const managedProjectIds = unique(
    acting.filter((b) => b.kind === "project" && b.role === "project_admin").map((b) => b.scopeId),
  );

  // Direct project bindings, plus every project inside an administered unit —
  // a BU Admin governs its unit's projects without being a member of each.
  const projectIds = unique([
    ...acting.filter((b) => b.kind === "project").map((b) => b.scopeId),
    ...PROJECTS.filter(
      (p) => p.workspaceId && managedBusinessUnitIds.includes(String(p.workspaceId)),
    ).map((p) => String(p.id)),
  ]);

  /**
   * Units held directly — and the parent unit of a project ONLY when it is a
   * unit they already belong to.
   *
   * A CROSS-UNIT LOAN MUST NOT DRAG ITS UNIT ALONG. A contributor lent to one
   * project in another Business Unit reaches that project and nothing else: not
   * the unit's other projects, not its budget, connectors, models or people.
   * Inheriting the parent unit from any project binding is exactly how the loan
   * would have widened into unit-wide read the moment it was granted — the
   * borrowing unit's whole estate, from one project seat.
   *
   * The last clause is the fallback for someone seated on a project with no
   * unit membership at all. It cannot happen through `addProjectMember`, which
   * enrols them, but a scope that answered "nothing" for such a person would
   * blank their own project rather than fail safe in any useful sense.
   */
  const ownUnitIds = acting.filter((b) => b.kind === "business_unit").map((b) => b.scopeId);
  const businessUnitIds = unique(
    ownUnitIds.length > 0
      ? ownUnitIds
      : acting
          .filter((b) => b.kind === "project" && b.parentId)
          .map((b) => b.parentId as string),
  );

  const level: ScopeKind = managedBusinessUnitIds.length > 0 ? "business_unit" : "project";

  return {
    level,
    isOrgWide: false,
    businessUnitIds,
    managedBusinessUnitIds,
    projectIds,
    managedProjectIds,
    actingBindings: acting,
    allBindings,
    identityId,
  };
}

function unique(values: string[]): string[] {
  return [...new Set(values)];
}

// ─── Filters (shared by route handlers and MSW so both agree) ─────────────────

export function canReadBusinessUnit(scope: AccessScope, workspaceId: string | null | undefined) {
  if (scope.isOrgWide) return true;
  if (!workspaceId) return false;
  return scope.businessUnitIds.includes(String(workspaceId));
}

export function canManageBusinessUnit(scope: AccessScope, workspaceId: string | null | undefined) {
  if (scope.isOrgWide) return true;
  if (!workspaceId) return false;
  return scope.managedBusinessUnitIds.includes(String(workspaceId));
}

export function canReadProject(scope: AccessScope, projectId: string | null | undefined) {
  if (scope.isOrgWide) return true;
  if (!projectId) return false;
  return scope.projectIds.includes(String(projectId));
}

export function canManageProject(scope: AccessScope, projectId: string | null | undefined) {
  if (scope.isOrgWide) return true;
  if (!projectId) return false;
  return scope.managedProjectIds.includes(String(projectId));
}

/**
 * May the viewer see a governance approval for this (unit, project) pair?
 *
 * Governance approvals are administrative artifacts, so plain READ access to a
 * unit is deliberately NOT enough: a Project Admin can read their parent Business
 * Unit for context, and that would otherwise have disclosed its budget-increase
 * requests — the unit's spend negotiations, which are none of their business.
 *
 * The second clause is what keeps the rule from over-tightening: some governance
 * types are project-scoped (`agent_default_project` routes to the Project Admin),
 * so an approval targeting a project the viewer ADMINISTERS is theirs even when
 * the unit above it is not.
 */
export function canReadGovernanceApproval(
  scope: AccessScope,
  workspaceId: string | null | undefined,
  projectId: string | null | undefined,
): boolean {
  if (scope.isOrgWide) return true;
  return canManageBusinessUnit(scope, workspaceId) || canManageProject(scope, projectId);
}

/**
 * Keep only the rows whose project the viewer may read.
 *
 * Rows with NO project id are dropped for a scoped viewer rather than kept:
 * an unattributable approval, trace or audit entry is exactly the row that
 * must not leak across a boundary, and "we couldn't tell whose it was" is not
 * a reason to show it to everyone.
 */
export function filterByProject<T>(
  scope: AccessScope,
  rows: readonly T[],
  projectIdOf: (row: T) => string | null | undefined,
): T[] {
  if (scope.isOrgWide) return [...rows];
  return rows.filter((row) => canReadProject(scope, projectIdOf(row)));
}

/** Keep only the rows belonging to a Business Unit the viewer may read. */
export function filterByBusinessUnit<T>(
  scope: AccessScope,
  rows: readonly T[],
  workspaceIdOf: (row: T) => string | null | undefined,
): T[] {
  if (scope.isOrgWide) return [...rows];
  return rows.filter((row) => canReadBusinessUnit(scope, workspaceIdOf(row)));
}
