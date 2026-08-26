import { z } from "zod";

import { ProjectId, TenantId } from "./ids";
import { AgentType, DeliveryTrack, LlmProvider, ModelTier, Phase, Status } from "./enums";
import { Timestamp } from "./primitives";
import { BudgetWindowFields } from "./budget-window";
import { UserRef } from "./user";

/**
 * Technical starting shape of the repository. Distinct from `DeliveryTrack`,
 * which selects the *delivery template* — the agent roster, gates and
 * required evidence (PRD §6). A Track 1 greenfield project and a Track 3
 * modernization project can both be `microservice`.
 */
export const ProjectTemplate = z.enum(["web_app", "microservice", "data_pipeline", "blank"]);
export type ProjectTemplate = z.infer<typeof ProjectTemplate>;

export const PhasePipelineEntry = z.object({
  phase: Phase,
  status: Status,
  updatedAt: Timestamp.optional(),
});
export type PhasePipelineEntry = z.infer<typeof PhasePipelineEntry>;

export const AgentModelBinding = z.object({
  agent: AgentType,
  provider: LlmProvider,
  model: z.string(),
  tier: ModelTier,
});
export type AgentModelBinding = z.infer<typeof AgentModelBinding>;

export const ProjectPolicy = z.object({
  requireApprovalOnPhase: z.array(Phase),
  branchProtection: z.object({
    enabled: z.boolean(),
    requiredReviewers: z.number().int().min(0).max(10),
  }),
  agentModels: z.array(AgentModelBinding),
  budgetCapUsd: z.number().nonnegative().nullable(),
});
export type ProjectPolicy = z.infer<typeof ProjectPolicy>;

/**
 * Project-creation approval state (PRD-aligned governance addition,
 * 2026-07-29). A Project Admin's creation is `pending_approval` until the
 * owning Business Unit's Admin approves it; Org Admin and BU Admin create
 * directly into `active` — they're at or above the approving tier already.
 * Existing/legacy projects default to `active` (backward compatible).
 */
export const ProjectApprovalStatus = z.enum(["active", "pending_approval", "rejected"]);
export type ProjectApprovalStatus = z.infer<typeof ProjectApprovalStatus>;

/**
 * Where the project itself stands as a piece of delivery work — the answer to
 * "how's it going", set by hand.
 *
 * A third status axis on purpose, because the two that already exist answer
 * different questions and neither can stand in for this one:
 *   - `approvalStatus` — may this project exist at all (governance).
 *   - `pipeline[].status` — what the agents are doing right now (machine
 *     state; a project can be `on_hold` with a run still finishing).
 *   - `deliveryStatus` — what the humans say the project's state is.
 *
 * Deriving it from the pipeline was the tempting alternative and is wrong: a
 * project with no runs this week is not automatically "on hold", and one whose
 * last phase merged is not automatically "completed" — only a person knows
 * which. Defaults to `not_started` so projects created before this existed
 * keep parsing.
 */
export const ProjectDeliveryStatus = z.enum([
  "not_started",
  "in_progress",
  "on_hold",
  "completed",
]);
export type ProjectDeliveryStatus = z.infer<typeof ProjectDeliveryStatus>;

export const PROJECT_DELIVERY_STATUS_LABEL: Record<ProjectDeliveryStatus, string> = {
  not_started: "Not started",
  in_progress: "In progress",
  on_hold: "On hold",
  completed: "Completed",
};

/** Display order — lifecycle order, not alphabetical. */
export const PROJECT_DELIVERY_STATUS_ORDER: ProjectDeliveryStatus[] = [
  "not_started",
  "in_progress",
  "on_hold",
  "completed",
];

/**
 * A connector/MCP server's access mode when assigned to a stage — lets a
 * creator declare, e.g., "GitHub is read-only for QA's stage but read-write
 * for Development's" rather than one blanket grant per tool. Keyed by a
 * composite `"{agentId}::{toolType}::{toolId}"` string in
 * `Project.toolAccessModes` (additive alongside the existing
 * `mcpServers`/`connectors` presence maps, which still just say "assigned or
 * not" — see components/app/tools-stage-picker.tsx::accessModeKey()).
 */
export const ToolAccessMode = z.enum(["read", "write", "both"]);
export type ToolAccessMode = z.infer<typeof ToolAccessMode>;

export const Project = z.object({
  id: ProjectId,
  tenantId: TenantId,
  name: z.string().min(1).max(200),
  slug: z.string().regex(/^[a-z0-9-]+$/),
  description: z.string().max(2000).nullish(),
  /**
   * The Business Unit (workspace) this project belongs to (PRD §12). Nullable
   * so a project can exist unassigned; the Projects screen groups by this and
   * falls back to an "Unassigned" section when null.
   */
  workspaceId: z.string().nullable().optional(),
  approvalStatus: ProjectApprovalStatus.default("active"),
  /** Human-set delivery state — see ProjectDeliveryStatus above. */
  deliveryStatus: ProjectDeliveryStatus.default("not_started"),
  /**
   * Set when a settings edit was QUEUED rather than applied.
   *
   * A Project Admin editing their own project raises a request their Business
   * Unit Admin decides, so the project in that response is UNCHANGED and the UI
   * must say "sent for approval" rather than "saved". Absent on every other
   * response, including a BU/Org Admin's edit, which applies directly.
   */
  pendingApproval: z.boolean().optional(),
  pendingRequestId: z.string().nullable().optional(),
  pendingApproverRole: z.string().nullable().optional(),
  approvalDecidedBy: z.string().nullable().optional(),
  approvalDecidedAt: Timestamp.nullable().optional(),
  approvalReason: z.string().nullable().optional(),
  template: ProjectTemplate,
  /**
   * The delivery track this project runs (PRD §6, FR-01). Drives the agent
   * roster, the pipeline rail and the Orchestrator stage diagram via
   * `lib/tracks.ts::agentsForTrack()`.
   *
   * Defaults to `greenfield` so projects created before tracks existed keep
   * their current eight-agent roster rather than failing to parse.
   */
  track: DeliveryTrack.default("greenfield"),
  archived: z.boolean(),
  owners: z.array(UserRef),
  pipeline: z.array(PhasePipelineEntry),
  /** Per-project stage→MCP-server mapping {agent_id: [mcp_server_id, ...]}. */
  mcpServers: z.record(z.string(), z.array(z.string())).optional(),
  /** Per-project stage→connector-kind mapping {agent_id: [connector_kind, ...]}. */
  connectors: z.record(z.string(), z.array(z.string())).optional(),
  /** Access mode per assigned tool — see ToolAccessMode above. Unset entries
   *  (e.g. projects created before this existed) default to "both" in the UI. */
  toolAccessModes: z.record(z.string(), ToolAccessMode).optional(),
  /** Monthly cost cap; null = inherit workspace / unlimited (migration 0032). */
  monthlyBudgetUsd: z.number().nonnegative().nullable().optional(),
  /** The period that cap is valid for — see lib/schemas/budget-window.ts. */
  ...BudgetWindowFields,
  /** Current calendar-month spend from the durable usage rollup. */
  monthlySpendUsd: z.number().nonnegative().optional(),
  lastActivityAt: Timestamp,
  createdAt: Timestamp,
});
export type Project = z.infer<typeof Project>;

export const ProjectCreateInput = z.object({
  name: z.string().min(1).max(200),
  template: ProjectTemplate,
  /** Delivery track chosen at creation (PRD §6). */
  track: DeliveryTrack.default("greenfield"),
  description: z.string().max(2000).optional(),
  /** The Business Unit this project belongs to — required so a Project
   *  Admin's creation routes to the right BU Admin for approval. */
  workspaceId: z.string().min(1),
  /** Contributors assigned at creation time — {email, roleName} pairs; each
   *  gets that role's agent access on this project automatically via
   *  AGENT_OWNERSHIP. Reuses the same onboarding primitive as the Members
   *  page, so an unrecognized email is onboarded, never rejected. */
  contributors: z
    .array(
      z.object({
        email: z.string().email(),
        roleName: z.string().min(1),
        /**
         * Agents granted on top of what `roleName` already reaches
         * (`AGENT_OWNERSHIP`). Optional and additive — absent means "the
         * role's own roster", which is what every contributor added before
         * this field existed meant, so old payloads keep their meaning.
         */
        extraAgents: z.array(z.string()).optional(),
      }),
    )
    .optional(),
  /** Stage→MCP-server mapping {agent_id: [mcp_server_id, ...]} chosen at creation. */
  mcp_servers: z.record(z.string(), z.array(z.string())).optional(),
  /** Stage→connector-kind mapping {agent_id: [connector_kind, ...]} chosen at creation. */
  connectors: z.record(z.string(), z.array(z.string())).optional(),
  /** Access mode per assigned tool, chosen at creation — see ToolAccessMode. */
  tool_access_modes: z.record(z.string(), ToolAccessMode).optional(),
  /**
   * Monthly cost cap set by the Project Admin at creation. Optional, and null
   * by default = inherit the Business Unit's cap. The Project Admin can raise
   * it later from the project's Cost page; over-allocating against the parent
   * unit's cap warns rather than blocks (see components/app/budget-hub.tsx).
   */
  monthlyBudgetUsd: z.number().nonnegative().nullable().optional(),
  ...BudgetWindowFields,
});
export type ProjectCreateInput = z.infer<typeof ProjectCreateInput>;
