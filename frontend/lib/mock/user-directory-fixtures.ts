/**
 * Cross-scope user-detail aggregation for the Users page's click-through
 * view — joins a single identity's Business-Unit memberships
 * (workspace-fixtures.ts) and project memberships (project-membership-
 * fixtures.ts), then resolves every distinct role they hold to its
 * permissions and agent-access levels (built-in via lib/roles.ts /
 * lib/auth/role-permissions.ts, or a custom role via custom-role-fixtures.ts).
 * Plain data + functions, server-safe. This is the DUMMY-DATA source; a real
 * backend replaces the route-handler body, not this shape.
 */
import {
  getIdentityBySsoSubject,
  getWorkspace,
  listIdentities,
  listMembershipsForIdentity,
} from "./workspace-fixtures";
import { getProjectById } from "./project-fixtures";
import { listProjectMembershipsForIdentity } from "./project-membership-fixtures";
import { listCustomRoles } from "./custom-role-fixtures";
import { listCrossBuGrants } from "./cross-bu-fixtures";
import { getOrgRole } from "./org-role-fixtures";
import { awaitsBusinessUnitRole, AGENT_OWNERSHIP, ROLE_META, type PlatformRole } from "@/lib/roles";
import { ROLE_PERMISSIONS } from "@/lib/auth/role-permissions";
import { PERMISSION_CATALOG } from "@/lib/auth/permission-catalog";
import { PHASE_LABEL } from "@/lib/agents";
import { Phase } from "@/lib/schemas/enums";
import type {
  DirectoryBinding,
  DirectoryEntry,
  RoleSummary,
  UserDetail,
  UserDetailBinding,
} from "@/lib/schemas/user-directory";

const PERMISSION_LABELS = new Map(
  PERMISSION_CATALOG.flatMap((g) =>
    g.perms.map((p) => [p.id, { label: p.label, grants: p.grants ?? null }]),
  ),
);

function permissionSummary(id: string) {
  const meta = PERMISSION_LABELS.get(id);
  return { id, label: meta?.label ?? id, grants: meta?.grants ?? null };
}

function buildRoleSummary(role: string): RoleSummary {
  if (role in ROLE_META) {
    const r = role as PlatformRole;
    const meta = ROLE_META[r];
    return {
      role,
      label: meta.label,
      isCustom: false,
      tier: meta.tier,
      permissions: (ROLE_PERMISSIONS[r] ?? []).map(permissionSummary),
      agentAccess: Phase.options
        .map((phase) => ({ phase, label: PHASE_LABEL[phase], level: AGENT_OWNERSHIP[r][phase] }))
        .filter((row) => row.level !== "none"),
    };
  }

  const custom = listCustomRoles().find((c) => c.id === role);
  if (custom) {
    return {
      role,
      label: custom.name,
      isCustom: true,
      tier: null,
      permissions: custom.permissions.map(permissionSummary),
      agentAccess: Phase.options
        .map((phase) => ({
          phase,
          label: PHASE_LABEL[phase],
          level: custom.agentAccess?.[phase] ?? "none",
        }))
        .filter((row) => row.level !== "none"),
    };
  }

  // Unknown role string (e.g. a deleted custom role still referenced by an
  // old membership) — render best-effort rather than dropping the binding.
  return {
    role,
    label: role.replace(/_/g, " "),
    isCustom: false,
    tier: null,
    permissions: [],
    agentAccess: [],
  };
}

/**
 * Every person in the organisation, with their org-level appointment and every
 * role they hold anywhere.
 *
 * ONE CALL, replacing the Users page's old fan-out of one query per Business
 * Unit plus one per project — roughly a dozen requests to render a table, each
 * scope-filtered independently, which is why a Business Unit Admin could only
 * ever see their own unit's people. The join belongs on the server: it is the
 * only place that can see the whole roster and still say, per row, which unit
 * it belongs to.
 */
export function listUserDirectory(): DirectoryEntry[] {
  return listIdentities()
    .map((identity) => {
      const unitBindings: DirectoryBinding[] = listMembershipsForIdentity(identity.id).map((m) => ({
        scope: "business_unit" as const,
        id: String(m.workspaceId),
        name: getWorkspace(m.workspaceId)?.displayName ?? String(m.workspaceId),
        businessUnitId: String(m.workspaceId),
        role: m.role,
        status: m.status,
      }));

      const projectBindings: DirectoryBinding[] = listProjectMembershipsForIdentity(
        identity.id,
      ).map((m) => {
        const project = getProjectById(m.projectId);
        return {
          scope: "project" as const,
          id: m.projectId,
          name: project?.name ?? m.projectId,
          businessUnitId: project?.workspaceId ? String(project.workspaceId) : null,
          role: m.role,
          status: m.status,
        };
      });

      const orgRole = getOrgRole(identity.id);
      // The unit the org-level record names, falling back to the one they are
      // actually a member of — the fallback covers a person added straight to a
      // unit by some other path, who would otherwise read as belonging nowhere.
      //
      // NOT for an Organization Admin, whose authority is org-wide and who
      // holds a membership row in every unit: taking the first would print
      // "Payments" beside them and read as the one unit they run.
      const businessUnitId =
        orgRole?.role === "org_admin"
          ? null
          : (orgRole?.businessUnitId ?? unitBindings[0]?.businessUnitId ?? null);

      // The role held in the unit they belong to. Falls back to any unit
      // binding they have, so a person whose org record and memberships
      // disagree still reads as something rather than as nothing.
      const unitRole =
        orgRole?.role === "org_admin"
          ? null
          : (unitBindings.find((b) => b.businessUnitId === businessUnitId)?.role ??
            unitBindings[0]?.role ??
            null);

      return {
        userId: identity.ssoSubject,
        identityId: String(identity.id),
        displayName: identity.displayName,
        email: identity.email,
        initials: identity.initials,
        orgRole: orgRole?.role ?? "contributor",
        unitRole,
        businessUnitId,
        businessUnitName: businessUnitId
          ? (getWorkspace(businessUnitId)?.displayName ?? businessUnitId)
          : null,
        bindings: [...unitBindings, ...projectBindings],
        // In a unit and holding nothing but the placeholder there. A project
        // role does not clear it: the queue asks the UNIT admin for a UNIT
        // role, and someone carrying only a project binding still has no
        // standing in the unit that owns them.
        awaitingRole:
          unitBindings.length > 0 && unitBindings.every((b) => awaitsBusinessUnitRole(b.role)),
        // Set per viewer in `scopeUserDirectory` — org-wide, nobody is a guest.
        isGuest: false,
      } satisfies DirectoryEntry;
    })
    .sort((a, b) => a.displayName.localeCompare(b.displayName));
}

/**
 * The directory as one viewer may see it.
 *
 * READING AND MANAGING ARE TWO DIFFERENT QUESTIONS, and this function only
 * answers the first. Anyone who ADMINISTERS a Business Unit — the Organization
 * Admin, or a Business Unit Admin — reads the whole organisation. Everyone else
 * sees the people in the unit they belong to.
 *
 * WHY A UNIT ADMIN GETS THE WHOLE ROSTER. "Who else is here" is a question they
 * genuinely have to answer: they borrow contributors from other units, they
 * hand people over to other units, and the borrow dialog identifies a person by
 * EMAIL precisely because it cannot offer a picker. A directory that stopped at
 * their own boundary made that address unfindable anywhere in the product. The
 * containment that matters is on the WRITE, not the read: `canAssignRole` in
 * app/(app)/users/page.tsx offers the role control only for units the viewer
 * administers, every other row renders "view only", and the membership
 * endpoints check `canManageBusinessUnit` on their own.
 *
 * A Project Admin is NOT included. They hold `member:manage` for their own
 * projects' rosters, which is a project-scoped power; the organisation's people
 * list is not theirs, and the project's own Members screen is where their
 * version of this question is answered.
 *
 * MEMBERSHIP OF A UNIT, NOT WORK INSIDE ONE — on BOTH sides of the match, for
 * the scoped viewers who remain.
 *
 * On the viewer's side: the units they are a member of, not the units their
 * projects happen to live in. On the listed person's side: their business-unit
 * bindings, not their project ones. The same confusion produced two different
 * leaks — a Project Admin with projects in Payments and Lending reading both
 * units' rosters, and a Platform Engineering contributor appearing in Lending's
 * list because of one project, with "Platform Engineering" printed next to
 * their name. Who works on a project is the project's own roster; this page
 * answers who belongs to a unit.
 *
 * A person with no unit at all (a Business Unit Admin appointed before one was
 * chosen) is visible only org-wide. They belong to no unit a scoped viewer can
 * match against, and showing them to everyone because they matched nothing is
 * how an unplaceable row becomes the one that leaks.
 */
export function scopeUserDirectory(scope: {
  isOrgWide: boolean;
  businessUnitIds: string[];
  managedBusinessUnitIds?: string[];
  actingBindings?: { kind: string; scopeId: string }[];
}): DirectoryEntry[] {
  const all = listUserDirectory();
  if (scope.isOrgWide) return all;

  /**
   * People from other units who are working HERE, on loan.
   *
   * A Business Unit Admin's list is the people in their unit — and a borrowed
   * contributor is not one, which is exactly why they were missing from it. But
   * they are working on this unit's project, against this unit's budget, and
   * the admin accountable for that had no way to see them at all: not in their
   * own directory, and not in anyone else's they can read. So they are listed,
   * marked as guests, and not editable — the unit that lent them still owns
   * them, and their role here was fixed by the approval.
   */
  const managed = new Set((scope.managedBusinessUnitIds ?? []).map(String));
  const guests = new Map<string, string>();
  if (managed.size > 0) {
    for (const g of listCrossBuGrants()) {
      if (managed.has(String(g.targetWorkspaceId))) guests.set(g.identityId, g.targetWorkspaceId);
    }
  }

  /**
   * A Business Unit Admin reads the organisation.
   *
   * Administering a unit is the line, not membership of one: it is what
   * distinguishes them from a Project Admin, who also holds `member:manage` but
   * holds it over project rosters rather than over people. The guest marks are
   * still applied on the way out — org-wide visibility does not make "this
   * person is on loan INTO your unit" a less useful thing to say, and it is the
   * only signal that separates a name you may act on from one you may not.
   */
  if (managed.size > 0) {
    return all.map((entry) => (guests.has(entry.identityId) ? { ...entry, isGuest: true } : entry));
  }

  /**
   * The units the viewer is a MEMBER of — not `scope.businessUnitIds`.
   *
   * That set is deliberately wider: it adds the parent unit of every project
   * they work on, as read context so a project page can name its unit and cap.
   * Used here it made a Project Admin with projects in two units the reader of
   * both units' rosters — which is how someone who belongs to Payments ends up
   * looking at Lending's people. Belonging to a unit is a membership, and a
   * project in a unit is not one.
   *
   * The fallback covers a person seated on a project with no unit membership
   * at all: read context is all they have, and an honest-but-blank directory
   * would be worse than the wider list they had before.
   */
  const memberOf = (scope.actingBindings ?? [])
    .filter((b) => b.kind === "business_unit")
    .map((b) => String(b.scopeId));
  const readable = new Set(memberOf.length > 0 ? memberOf : scope.businessUnitIds.map(String));

  return all.flatMap((entry) => {
    if (entry.businessUnitId !== null && readable.has(entry.businessUnitId)) return [entry];

    // Listed as a guest, under the unit they actually belong to — claiming they
    // are in yours would be the lie the marker exists to avoid.
    if (guests.has(entry.identityId)) return [{ ...entry, isGuest: true }];

    const shared = entry.bindings.find(
      (b) =>
        b.scope === "business_unit" && b.businessUnitId !== null && readable.has(b.businessUnitId),
    );
    if (!shared) return [];

    /**
     * The Organization Admin stays UNPLACED, in every list they appear in.
     *
     * They hold a membership row in every unit, so the relabelling below found
     * one and printed the viewer's own unit beside their name — a Business Unit
     * Admin's roster claimed the Organization Admin as one of their people.
     * `listUserDirectory` already refuses to pick a unit for them for exactly
     * this reason (see `businessUnitId` above); undoing that here, per viewer,
     * put the claim back one scope at a time.
     *
     * Visible, because org-wide authority is a fact about the organisation the
     * unit sits in and a missing row reads as a missing person. Unplaced,
     * because no unit owns them. Their appointment chip already says
     * Organization Admin, so the empty unit column reads as scope, not a gap.
     */
    if (entry.orgRole === "org_admin") return [entry];

    /**
     * Name the unit the VIEWER shares, not the person's home one.
     *
     * Someone can belong to two units — a DevOps Engineer in Lending who is
     * also in Platform Engineering — and the row is in view because of the
     * second. Printing the first put "Lending" on a Platform Engineering
     * admin's screen, which reads as exactly the other-unit member the filter
     * above just removed, and sends the reader looking for a leak that isn't
     * there. The role travels with the unit, because a role IS a binding.
     */
    return [
      {
        ...entry,
        unitRole: shared.role,
        businessUnitId: shared.businessUnitId,
        businessUnitName: shared.name,
      },
    ];
  });
}

export function getUserDetail(ssoSubject: string): UserDetail | undefined {
  const identity = getIdentityBySsoSubject(ssoSubject);
  if (!identity) return undefined;

  const workspaceBindings: UserDetailBinding[] = listMembershipsForIdentity(identity.id).map(
    (m) => {
      const ws = getWorkspace(m.workspaceId);
      return {
        scope: "workspace",
        id: m.workspaceId,
        name: ws?.displayName ?? m.workspaceId,
        parentName: null,
        role: m.role,
        status: m.status,
      };
    },
  );

  const projectBindings: UserDetailBinding[] = listProjectMembershipsForIdentity(identity.id).map(
    (m) => {
      const project = getProjectById(m.projectId);
      const parentWorkspace = project?.workspaceId ? getWorkspace(project.workspaceId) : undefined;
      return {
        scope: "project",
        id: m.projectId,
        name: project?.name ?? m.projectId,
        parentName: parentWorkspace?.displayName ?? null,
        role: m.role,
        status: m.status,
      };
    },
  );

  const distinctRoles = [...new Set([...workspaceBindings, ...projectBindings].map((b) => b.role))];

  return {
    userId: identity.ssoSubject,
    displayName: identity.displayName,
    email: identity.email,
    initials: identity.initials,
    workspaceBindings,
    projectBindings,
    roleSummaries: distinctRoles.map(buildRoleSummary),
  };
}
