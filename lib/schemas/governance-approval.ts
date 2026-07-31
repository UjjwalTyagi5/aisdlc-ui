import { z } from "zod";

import { Timestamp } from "./primitives";

/**
 * Governance-tier approvals — project creation and model-credential
 * onboarding, both routed to a Business Unit Admin. Deliberately a SEPARATE
 * type family from `ApprovalGate` (lib/schemas/approval.ts): that schema
 * requires `runId`/`projectId`/`phase` (all non-nullable) and resolves via a
 * run-signal transport, because every existing gate is tied to an agent run.
 * A project-creation or model-credential approval isn't — there's no run to
 * signal. Retrofitting optional run/phase fields onto `ApprovalGate` would
 * risk breaking every consumer that assumes a real one (the phase chip, the
 * "Open run" link, the run-signal decision path). Merged into the same
 * `/approvals` inbox at the UI layer instead (`approval-queue.tsx`), so a BU
 * Admin sees everything in one place without the schemas needing to unify.
 */
/**
 * `budget_increase` is the first type routed to `org_admin` rather than
 * `bu_admin` (a BU Admin requests it for their own business unit) — see
 * lib/governance.ts::GOVERNANCE_APPROVER_ROLE. Every other approval type
 * still routes sideways to the BU Admin; this one alone goes up a tier.
 */
/**
 * `agent_default_org`/`agent_default_workspace`/`agent_default_project` route
 * to that tier's owner (org_admin / bu_admin / project_admin respectively) —
 * proposing a change to a shared Agent Studio default you don't own (PRD
 * addition: the Org → Business Unit → Project → Personal cascade). Personal
 * defaults never need this — every person owns their own tier outright.
 */
export const GovernanceApprovalType = z.enum([
  "project_creation",
  "model_credential",
  "budget_increase",
  "project_archive",
  "agent_default_org",
  "agent_default_workspace",
  "agent_default_project",
]);
export type GovernanceApprovalType = z.infer<typeof GovernanceApprovalType>;

export const GovernanceApprovalStatus = z.enum(["pending", "approved", "rejected"]);
export type GovernanceApprovalStatus = z.infer<typeof GovernanceApprovalStatus>;

export const GovernanceApproval = z.object({
  id: z.string(),
  type: GovernanceApprovalType,
  status: GovernanceApprovalStatus,
  workspaceId: z.string(),
  workspaceName: z.string(),
  projectId: z.string().nullable(),
  projectName: z.string().nullable(),
  title: z.string(),
  summary: z.string(),
  requestedBy: z.string(),
  requestedAt: Timestamp,
  decidedBy: z.string().nullable(),
  decidedAt: Timestamp.nullable(),
  reason: z.string().max(2000).nullable(),
  /** The project id, model-credential id, or workspace id (for
   *  budget_increase) this decision applies to. */
  targetRef: z.string(),
  /** Type-specific structured data the decide step needs to apply the
   *  decision — e.g. `{ requestedAmountUsd }` for budget_increase. Avoids
   *  growing a type-specific optional field per new approval type. */
  payload: z.record(z.string(), z.unknown()).nullable().optional(),
});
export type GovernanceApproval = z.infer<typeof GovernanceApproval>;

export const GovernanceApprovalDecision = z.enum(["approve", "reject"]);
export type GovernanceApprovalDecision = z.infer<typeof GovernanceApprovalDecision>;

export const GovernanceApprovalDecisionInput = z.object({
  decision: GovernanceApprovalDecision,
  reason: z.string().max(2000).optional(),
});
export type GovernanceApprovalDecisionInput = z.infer<typeof GovernanceApprovalDecisionInput>;
