/**
 * Copilot stage catalog — the single source of truth for the pipeline rail.
 *
 * Keyed by the BACKEND stage ids the Copilot WS emits over `stage.changed`
 * (`shared/services/orchestrator/progression.py::STAGE_ORDER` +
 * `gate_routing.py::GATE_OWNER`). Note the id is `code_review` here (the WS
 * vocabulary), whereas the REST `Phase` enum spells the same stage `review` —
 * we key to the WS id so `activeStage` comparisons are exact.
 */
import { AGENT_OWNER_ROLE, ROLE_META } from "@/lib/roles";
import type { Phase } from "@/lib/schemas/enums";

/** Owning-role label for a phase, from the PRD ownership matrix (§14.7). */
function prdOwnerLabel(phase: Phase): string {
  return ROLE_META[AGENT_OWNER_ROLE[phase]].label;
}

export type StageStatusDot =
  | "idle"
  | "interviewing"
  | "running"
  | "awaiting_gate"
  | "approved"
  | "rejected"
  | "complete";

export interface CopilotStage {
  /** Backend stage id (WS `stage.changed` value). */
  id: string;
  label: string;
  /** Default gate-owner role label (blueprint §4.3). "auto" → auto-approved. */
  ownerRole: string;
  /** Whether the gate is a mandatory (never auto-approvable) sign-off. */
  mandatory?: boolean;
  auto?: boolean;
}

/**
 * 8 stages in pipeline order — mirrors STAGE_ORDER.
 *
 * Owner roles come from the PRD ownership matrix (§14.7) via
 * `ownerRoleLabel()`, so they can only ever name one of the platform's twelve
 * roles. The security and release sign-offs are the two mandatory checkpoints
 * that cannot be waived (§44.5).
 */
export const COPILOT_STAGES: readonly CopilotStage[] = [
  { id: "requirements", label: "Requirements", ownerRole: prdOwnerLabel("requirements") },
  { id: "design", label: "Design", ownerRole: prdOwnerLabel("design") },
  { id: "development", label: "Development", ownerRole: prdOwnerLabel("development") },
  { id: "code_review", label: "Code Review", ownerRole: prdOwnerLabel("review") },
  { id: "security", label: "Security", ownerRole: prdOwnerLabel("security"), mandatory: true },
  { id: "testing", label: "Testing", ownerRole: prdOwnerLabel("testing") },
  { id: "deployment", label: "Deployment", ownerRole: prdOwnerLabel("deployment"), mandatory: true },
  { id: "documentation", label: "Documentation", ownerRole: "Auto-approved", auto: true },
] as const;

export const STAGE_INDEX: Record<string, number> = Object.fromEntries(
  COPILOT_STAGES.map((s, i) => [s.id, i]),
);

export function stageLabel(id: string): string {
  return COPILOT_STAGES.find((s) => s.id === id)?.label ?? id;
}

/**
 * Map from the backend's snake_case gate owner roles to display labels.
 *
 * The backend still emits its legacy vocabulary on `gate.state` events. Each
 * legacy name is translated to the platform role that actually owns that
 * agent (PRD §14.7), so no screen shows a role the platform does not have.
 */
const ROLE_LABEL_MAP: Record<string, string> = {
  // Legacy backend names → the owning platform role.
  product_manager: prdOwnerLabel("requirements"), // BA owns Requirements
  tech_lead: prdOwnerLabel("design"), // Architect owns Design
  delivery_lead: prdOwnerLabel("security"), // Security Engineer owns Security
  qa_lead: prdOwnerLabel("testing"), // QA / Tester owns Testing
  sre_lead: prdOwnerLabel("deployment"), // DevOps Engineer owns Deployment
  security_auditor: prdOwnerLabel("security"),
  auto: "Auto-approved",

  // The platform's own role ids, so a correctly-emitted role passes through.
  ba: prdOwnerLabel("requirements"),
  architect: prdOwnerLabel("design"),
  qa: prdOwnerLabel("testing"),
  security_engineer: prdOwnerLabel("security"),
  devops_engineer: prdOwnerLabel("deployment"),
  data_engineer: prdOwnerLabel("data_engineering"),
  project_admin: prdOwnerLabel("documentation"),
};

/**
 * Prettify a raw owner role (e.g. "product_manager" → "Product Manager").
 * The gate.state event carries the snake_case role; the explicit map provides
 * consistent display labels aligned with the pipeline rail.
 */
export function ownerRoleLabel(role: string): string {
  const mapped = ROLE_LABEL_MAP[role.toLowerCase()];
  if (mapped) return mapped;
  const known = COPILOT_STAGES.find(
    (s) => s.ownerRole.toLowerCase().replace(/\s+/g, "_") === role.toLowerCase(),
  );
  if (known) return known.ownerRole;
  // Fall back to title-caser for unknown roles.
  return role
    .split(/[_\s]+/)
    .map((w) => (w ? w[0]!.toUpperCase() + w.slice(1) : w))
    .join(" ");
}

/**
 * Derive each stage's rail status from ACTUAL progress, not pipeline position.
 * The active stage takes the live status; any other stage that has genuinely
 * produced an artifact is "approved" (green); everything else is "idle"
 * (Upcoming). This is truthful in both directions: jumping straight to
 * Development leaves Requirements/Design as Upcoming (they never ran), and
 * switching back from Development to Requirements keeps Development green
 * because its artifact persists. `completedStages` is the set of stage ids
 * that have at least one persisted artifact.
 */
export function railStatusFor(
  stageId: string,
  activeStage: string,
  activeStatus: StageStatusDot,
  completedStages?: ReadonlySet<string>,
): StageStatusDot {
  if (stageId === activeStage) return activeStatus;
  if (completedStages?.has(stageId)) return "approved";
  return "idle";
}
