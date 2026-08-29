import { z } from "zod";

import { Phase } from "./enums";
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
/**
 * The last four are RAISED BY HAND from Requests & Approvals; the seven above
 * them are still only created as a side-effect of another flow (creating a
 * project, onboarding a credential, proposing an agent default…).
 *
 * The split matters to routing: the original seven are TYPE-routed — a project
 * creation always goes to the BU Admin, whoever asked — because the type alone
 * says which tier can grant it. A hand-raised request has no such rule, so it
 * is TIER-routed from whoever raised it (`lib/requests/routing.ts`). PRD §44.3
 * names exactly these filing points: "Model Management, Connectors, MCP
 * Servers, Cost & Budget, or Projects".
 */
export const GovernanceApprovalType = z.enum([
  "project_creation",
  "model_credential",
  "budget_increase",
  "project_archive",
  /**
   * A Project Admin edited their own project's settings. Raised BY the save, not
   * chosen from the picker: the edit is queued as a request for their Business
   * Unit Admin and applied on approval, rather than written straight to the
   * project. See `_queue_settings_change` in backend/shared/routers/projects.py.
   */
  "project_settings_change",
  "agent_default_org",
  "agent_default_workspace",
  "agent_default_project",
  "connector_access",
  "mcp_server",
  "agent_access",
  "access_request",
  "user_onboarding",
  /**
   * A Contributor was onboarded into a Business Unit and needs a role from its
   * admin. The one type that travels DOWNWARD — the Organization Admin raises
   * it and the unit's admin answers, because the answer is theirs to give and
   * not theirs to ask for. It is also the one type not closed by a plain
   * approve/reject: it closes when a role is actually assigned, from either
   * Users or this queue.
   */
  "role_assignment",
  /**
   * One Business Unit asking to borrow another's contributor for a single
   * project. Routed to the contributor's OWN (parent) unit admin — the only
   * person who can lend them — rather than to anyone above the asker. Approving
   * writes a `CrossBuGrant` and seats them; their parent unit never changes.
   */
  "cross_bu_assignment",
  "model_provider_access",
  "other",
]);
export type GovernanceApprovalType = z.infer<typeof GovernanceApprovalType>;

export const REQUEST_TYPE_LABEL: Record<GovernanceApprovalType, string> = {
  project_creation: "Project creation",
  model_credential: "Model credential",
  budget_increase: "Budget headroom",
  project_archive: "Project archive",
  project_settings_change: "Project settings change",
  agent_default_org: "Agent default — organization",
  agent_default_workspace: "Agent default — business unit",
  agent_default_project: "Agent default — project",
  connector_access: "Connector access",
  mcp_server: "MCP server",
  agent_access: "Agent access",
  access_request: "Access or permission",
  user_onboarding: "User onboarding",
  role_assignment: "Role assignment",
  cross_bu_assignment: "Cross-unit contributor",
  model_provider_access: "Model provider access",
  other: "Other",
};

/**
 * What the type means to the person raising it — shown under the picker.
 *
 * The same word means different things at different tiers, and the picker is
 * where that gets confusing: a Project Admin asking for a "model credential"
 * wants one granted to their project, while a Business Unit Admin asking about
 * models wants a provider onboarded for the whole organisation. Naming the ask
 * is cheaper than watching people file the wrong one.
 */
export const REQUEST_TYPE_HINT: Partial<Record<GovernanceApprovalType, string>> = {
  access_request: "A permission or scope you don't currently hold.",
  model_credential: "Make a model available to your project.",
  model_provider_access: "Onboard a provider for the whole organisation.",
  connector_access: "Connect a tool your work depends on.",
  mcp_server: "Add an MCP server to your project's toolset.",
  agent_access: "Work with an agent your role doesn't reach. Your Project Admin sees it first, then the agent's owner.",
  user_onboarding: "Bring someone onto the platform who isn't on it yet.",
  budget_increase: "Raise a spending cap that is about to bind.",
  project_creation: "Stand up a new project.",
  other: "Anything the list above doesn't cover.",
};

/**
 * The request lifecycle.
 *
 * `pending_review` replaces what was simply `pending`: with `submitted` now
 * distinct, "pending" no longer said which of the two it meant. The split is
 * real — `submitted` is "sent, not yet with a named approver", `pending_review`
 * is "sitting with one" — and it is what lets the queue tell a request nobody
 * has picked up from one somebody is deciding.
 *
 * `escalated` is a state, not just an event: a request that climbed a tier is
 * still open, but it is open somewhere else, and a queue that showed it as
 * plain `pending_review` would hide that the original approver never answered.
 *
 * `draft` and `cancelled` are the two ends the initiator controls — the only
 * transitions in this list that need no approver.
 */
export const GovernanceApprovalStatus = z.enum([
  "draft",
  "submitted",
  "pending_review",
  "approved",
  "rejected",
  "cancelled",
  "escalated",
]);
export type GovernanceApprovalStatus = z.infer<typeof GovernanceApprovalStatus>;

/** Open = still needs a decision. The queue's definition of "pending". */
export const OPEN_REQUEST_STATUSES: readonly GovernanceApprovalStatus[] = [
  "submitted",
  "pending_review",
  "escalated",
];

export const RequestPriority = z.enum(["low", "normal", "high", "urgent"]);
export type RequestPriority = z.infer<typeof RequestPriority>;

/**
 * An attachment's METADATA. There is no file behind it.
 *
 * This platform runs without a backend ([[no-backend-static-frontend]]), so
 * there is nowhere to put bytes. Recording name/type/size keeps the shape a
 * real storage layer would fill in, and the form says plainly that nothing is
 * stored — a fake upload that appeared to succeed would be worse than no
 * upload at all, because someone would rely on it.
 */
export const RequestAttachment = z.object({
  name: z.string().min(1).max(255),
  sizeBytes: z.number().int().nonnegative(),
  contentType: z.string().max(255),
});
export type RequestAttachment = z.infer<typeof RequestAttachment>;

export const RequestEventKind = z.enum([
  "created",
  "submitted",
  "assigned",
  "commented",
  "approved",
  "rejected",
  "escalated",
  "cancelled",
]);
export type RequestEventKind = z.infer<typeof RequestEventKind>;

/**
 * One entry in the audit trail (PRD FR-05: "a permanent, unchangeable record
 * of whatever was decided").
 *
 * Append-only by construction — nothing in the API edits or removes an event,
 * because the trail's value is precisely that it cannot be tidied up after the
 * fact. An escalation records the role it moved TO, so "why is a BU Admin
 * deciding a project request" is answerable from the request itself.
 */
export const RequestTimelineEvent = z.object({
  id: z.string(),
  kind: RequestEventKind,
  at: Timestamp,
  /** Display name of the person, or "System" for automatic transitions. */
  actor: z.string(),
  actorRole: z.string().nullable().optional(),
  /** The role the request moved to — `assigned` and `escalated` only. */
  toRole: z.string().nullable().optional(),
  note: z.string().max(2000).nullable().optional(),
});
export type RequestTimelineEvent = z.infer<typeof RequestTimelineEvent>;

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
  /** Identity of the initiator — powers "Raised by me" and the self-approval
   *  block, neither of which can be answered from a display name. */
  requestedById: z.string().nullable().optional(),
  /** The role the initiator held when raising it: what the upward chain is
   *  computed FROM (`lib/requests/routing.ts`). */
  requestedByRole: z.string().nullable().optional(),
  requestedAt: Timestamp,
  decidedBy: z.string().nullable(),
  decidedAt: Timestamp.nullable(),
  reason: z.string().max(2000).nullable(),
  /** Longer body. `summary` stays the one-line form the table row shows. */
  description: z.string().max(4000).nullable().optional(),
  priority: RequestPriority.default("normal"),
  attachments: z.array(RequestAttachment).default([]),
  timeline: z.array(RequestTimelineEvent).default([]),
  /** The role currently holding it — null once decided or cancelled. */
  currentApproverRole: z.string().nullable().optional(),
  /**
   * Where a multi-stage request has got to. Null for the single-decision types,
   * which is all of them but `agent_access`.
   *
   * An agent-access request is answered TWICE, by two people asking different
   * questions: the Project Admin decides whether this person should be doing
   * this work at all, and the agent's owner decides whether the agent should
   * be doing it for them. Neither can answer the other's question, so neither
   * approval alone grants access.
   *
   * This is a stage, deliberately, and not an escalation. An escalation means
   * the first approver did not answer; here they did, and the request moving
   * on is the process working. Recording them apart is what lets the queue and
   * the timeline say which happened.
   */
  approvalStage: z.enum(["project_admin", "agent_owner"]).nullable().optional(),
  /** How many tiers it has climbed. 0 = still with its first approver. */
  escalationCount: z.number().int().nonnegative().default(0),
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

/**
 * What a person may send when raising a request.
 *
 * Conspicuously ABSENT: the requester's identity, their role, and the approver.
 * All three are derived server-side from the session — a client that could name
 * its own role would pick the one whose chain is shortest, and a client that
 * could name its own approver would not need a chain at all. The whole routing
 * model is only worth writing if the request cannot choose its own answer.
 */
export const RequestCreateInput = z.object({
  type: GovernanceApprovalType,
  title: z.string().min(4, "Give the request a title").max(160),
  description: z.string().min(10, "Describe what you need and why").max(4000),
  priority: RequestPriority.default("normal"),
  workspaceId: z.string().min(1, "Choose a business unit"),
  projectId: z.string().nullable().optional(),
  attachments: z.array(RequestAttachment).max(10).default([]),
  /**
   * WHAT is being asked for, when the type needs one — currently the delivery
   * phase of an `agent_access` request.
   *
   * This is content, not routing, and the distinction is why it is allowed
   * here at all: the requester names the agent they want, and the platform
   * derives who owns it. A client naming its own approver is the hole the
   * docblock above guards; a client naming its own ask is just the ask.
   */
  phase: Phase.optional(),
  /**
   * WHAT is being asked for, when the type needs one — a connector kind, an
   * MCP server row id, or a model provider id. Same content-not-routing rule
   * as `phase`.
   */
  targetId: z.string().max(255).optional(),
  /** connector_access's access level (read/write/read_write) — only
   *  meaningful alongside targetId for this one type. */
  accessLevel: z.string().max(16).optional(),
  /** model_credential's target: a project's ask needs both a provider and a
   *  model id. */
  providerModel: z.object({ provider: z.string(), modelId: z.string() }).optional(),
  /** user_onboarding's target: who is being asked about. */
  onboardEmail: z.string().email().optional(),
});
export type RequestCreateInput = z.infer<typeof RequestCreateInput>;

export const RequestReasonInput = z.object({
  reason: z.string().max(2000).optional(),
});
export type RequestReasonInput = z.infer<typeof RequestReasonInput>;
