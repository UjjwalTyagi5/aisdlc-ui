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
  listMembershipsForIdentity,
} from "./workspace-fixtures";
import { getProjectById } from "./project-fixtures";
import { listProjectMembershipsForIdentity } from "./project-membership-fixtures";
import { listCustomRoles } from "./custom-role-fixtures";
import { AGENT_OWNERSHIP, ROLE_META, type PlatformRole } from "@/lib/roles";
import { ROLE_PERMISSIONS } from "@/lib/auth/role-permissions";
import { PERMISSION_CATALOG } from "@/lib/auth/permission-catalog";
import { PHASE_LABEL } from "@/lib/agents";
import { Phase } from "@/lib/schemas/enums";
import type { RoleSummary, UserDetail, UserDetailBinding } from "@/lib/schemas/user-directory";

const PERMISSION_LABELS = new Map(
  PERMISSION_CATALOG.flatMap((g) => g.perms.map((p) => [p.id, { label: p.label, grants: p.grants ?? null }])),
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
        .map((phase) => ({ phase, label: PHASE_LABEL[phase], level: custom.agentAccess?.[phase] ?? "none" }))
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

export function getUserDetail(ssoSubject: string): UserDetail | undefined {
  const identity = getIdentityBySsoSubject(ssoSubject);
  if (!identity) return undefined;

  const workspaceBindings: UserDetailBinding[] = listMembershipsForIdentity(identity.id).map((m) => {
    const ws = getWorkspace(m.workspaceId);
    return {
      scope: "workspace",
      id: m.workspaceId,
      name: ws?.displayName ?? m.workspaceId,
      parentName: null,
      role: m.role,
      status: m.status,
    };
  });

  const projectBindings: UserDetailBinding[] = listProjectMembershipsForIdentity(identity.id).map((m) => {
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
  });

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
