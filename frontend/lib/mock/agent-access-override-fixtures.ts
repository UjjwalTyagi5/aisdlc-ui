/**
 * Per-project agent-access overrides — lets a Project Admin grant (or
 * restrict) any role's access to any agent for just ONE project, on top of
 * that role's global default (lib/roles.ts::AGENT_OWNERSHIP, or a custom
 * role's own `agentAccess`). Plain data + functions, server-safe (imported
 * by both the Next.js route handlers and the MSW handlers — see
 * [[msw-dual-runtime-mutation-rule]]). This is the DUMMY-DATA source; a real
 * backend replaces the route-handler bodies, not these shapes.
 */
import type { AgentAccessOverride, InvolvementLevel } from "@/lib/schemas/agent-access";
import type { Phase } from "@/lib/schemas/enums";

let nextId = 1;
const OVERRIDES: AgentAccessOverride[] = [];

export function listOverridesForProject(projectId: string): AgentAccessOverride[] {
  return OVERRIDES.filter((o) => o.projectId === projectId);
}

/** Upserts the (project, role, phase) override — one entry per triple. */
export function setOverride(
  projectId: string,
  role: string,
  phase: Phase,
  involvement: InvolvementLevel,
  setBy: string,
): AgentAccessOverride {
  const existing = OVERRIDES.find(
    (o) => o.projectId === projectId && o.role === role && o.phase === phase,
  );
  if (existing) {
    existing.involvement = involvement;
    existing.setBy = setBy;
    existing.setAt = new Date().toISOString();
    return existing;
  }
  const created: AgentAccessOverride = {
    id: `override_${nextId++}`,
    projectId,
    role,
    phase,
    involvement,
    setBy,
    setAt: new Date().toISOString(),
  };
  OVERRIDES.push(created);
  return created;
}

/** Removes an override, reverting that (project, role, phase) to its global default. */
export function removeOverride(projectId: string, role: string, phase: Phase): boolean {
  const i = OVERRIDES.findIndex(
    (o) => o.projectId === projectId && o.role === role && o.phase === phase,
  );
  if (i === -1) return false;
  OVERRIDES.splice(i, 1);
  return true;
}
