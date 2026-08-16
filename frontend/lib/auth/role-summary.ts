/**
 * Role → what it actually grants. Static catalogue only, no fixtures.
 *
 * Lifted out of `lib/mock/user-directory-fixtures.ts` when that file stopped
 * being a data source. The distinction that made it worth lifting: a role's
 * permissions and agent-access levels are REFERENCE DATA — the same for every
 * tenant, shipped with the app, and true regardless of what any database holds —
 * while the people holding those roles are records. Only the second kind was
 * ever fake, so only the second kind had to go.
 *
 * Custom roles arrive as an argument rather than being read from a store, so the
 * caller decides where they come from (FastAPI `GET /admin/custom-roles`) and
 * this module keeps no I/O of its own.
 */
import { AGENT_OWNERSHIP, ROLE_META, type PlatformRole } from "@/lib/roles";
import { ROLE_PERMISSIONS } from "@/lib/auth/role-permissions";
import { PERMISSION_CATALOG } from "@/lib/auth/permission-catalog";
import { PHASE_LABEL } from "@/lib/agents";
import { Phase } from "@/lib/schemas/enums";
import type { InvolvementLevel } from "@/lib/schemas/agent-access";
import type { RoleSummary } from "@/lib/schemas/user-directory";

const PERMISSION_LABELS = new Map(
  PERMISSION_CATALOG.flatMap((g) =>
    g.perms.map((p) => [p.id, { label: p.label, grants: p.grants ?? null }]),
  ),
);

function permissionSummary(id: string) {
  const meta = PERMISSION_LABELS.get(id);
  return { id, label: meta?.label ?? id, grants: meta?.grants ?? null };
}

/** A custom role, in the only shape this builder needs from one. */
export interface CustomRoleLike {
  id: string;
  name: string;
  permissions: string[];
  agentAccess?: Partial<Record<Phase, InvolvementLevel>> | null;
}

export function buildRoleSummary(
  role: string,
  customRoles: readonly CustomRoleLike[] = [],
): RoleSummary {
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

  const custom = customRoles.find((c) => c.id === role);
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

  // Unknown role string (e.g. a deleted custom role still referenced by an old
  // binding) — render best-effort rather than dropping the binding, which would
  // make the person look unroled when they are merely holding something stale.
  return {
    role,
    label: role.replace(/_/g, " "),
    isCustom: false,
    tier: null,
    permissions: [],
    agentAccess: [],
  };
}
