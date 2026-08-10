import { z } from "zod";

import { Phase } from "./enums";
import { Timestamp } from "./primitives";

/**
 * Runtime-validated mirror of `lib/roles.ts::Involvement` — that file stays
 * zod-free (it's pure role-model constants, no I/O), so this schema exists
 * wherever an involvement level crosses an API boundary: a custom role's own
 * per-phase agent access, and a project-level override of ANY role's access
 * (lib/mock/agent-access-override-fixtures.ts). Keep the two in sync.
 */
export const InvolvementLevel = z.enum(["owner", "primary", "build", "requests", "use", "none"]);
export type InvolvementLevel = z.infer<typeof InvolvementLevel>;

/**
 * A per-project override of one role's access to one agent — e.g. granting
 * Architect `build` on Development in a single project, on top of that
 * role's global default in `AGENT_OWNERSHIP`. Never mutates the global
 * table; the effective level for a (project, role, phase) triple is the
 * override if one exists, else the global default (see
 * lib/mock/agent-access-override-fixtures.ts::effectiveInvolvement).
 */
export const AgentAccessOverride = z.object({
  id: z.string(),
  projectId: z.string(),
  /** A PlatformRole string, or a custom role's id (`role_...`). */
  role: z.string(),
  phase: Phase,
  involvement: InvolvementLevel,
  setBy: z.string(),
  setAt: Timestamp,
});
export type AgentAccessOverride = z.infer<typeof AgentAccessOverride>;

export const AgentAccessOverrideInput = z.object({
  role: z.string(),
  phase: Phase,
  involvement: InvolvementLevel,
});
export type AgentAccessOverrideInput = z.infer<typeof AgentAccessOverrideInput>;
