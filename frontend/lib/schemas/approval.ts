import { z } from "zod";

import { ApprovalId, ArtifactId, ProjectId, RunId, UserId } from "./ids";
import { AgentType, ApprovalDecision, CapabilityClass, Phase } from "./enums";
import { Timestamp } from "./primitives";

export const ApprovalEvent = z.object({
  id: ApprovalId,
  runId: RunId,
  artifactId: ArtifactId.nullable(),
  decision: ApprovalDecision,
  decidedBy: UserId,
  decidedAt: Timestamp,
  reason: z.string().max(2000).optional(),
});
export type ApprovalEvent = z.infer<typeof ApprovalEvent>;

export const ApprovalSubmitInput = z.object({
  decision: ApprovalDecision,
  reason: z.string().max(2000).optional(),
  /** Idempotency key — any retry of the same decision reconciles to one event. */
  idempotencyKey: z.string().min(1),
});
export type ApprovalSubmitInput = z.infer<typeof ApprovalSubmitInput>;

// ── Approvals inbox: cross-run pending-gate queue ────────────────────────────
// A *gate* is a run paused for a human: a phase approval or an agent
// clarification. Distinct from ApprovalEvent (the recorded decision). The inbox
// lists gates where the viewer holds `requiredPermission`.

/**
 * The three things that land in a personal queue (PRD §33.2, "Your queue —
 * what's waiting on you"):
 *
 *  - `approval`      An approval or sign-off — routed to the agent's owning
 *                    role, or the Project Admin as fallback.
 *  - `clarification` A decision the agent needs before it can continue —
 *                    routed to whoever is running that agent.
 *  - `outcome`       The result of something *you* raised: a sandbox
 *                    promotion approved or rejected, a request granted or
 *                    denied. Informational, not actionable.
 */
export const ApprovalGateType = z.enum(["approval", "clarification", "outcome"]);
export type ApprovalGateType = z.infer<typeof ApprovalGateType>;

/** The artifact under review (approval gates only). */
export const GateArtifactRef = z.object({
  id: ArtifactId,
  title: z.string(),
  type: z.string(),
});
export type GateArtifactRef = z.infer<typeof GateArtifactRef>;

export const ApprovalGate = z.object({
  /** Stable gate id (e.g. `${runId}:${phase}`) — the decision route param. */
  id: z.string().min(1),
  type: ApprovalGateType,
  runId: RunId,
  projectId: ProjectId,
  projectName: z.string(),
  phase: Phase,
  agentType: AgentType,
  /** Permission required to action this gate — drives role-scoping of the inbox. */
  requiredPermission: z.string(),
  /**
   * Human-readable role the gate is routed to. Always one of the platform's
   * twelve roles (PRD §33.1 plus Scrum Master) — derived from the ownership
   * matrix, never a free-text job title.
   */
  waitingForRole: z.string(),
  /**
   * Capability class of the action being gated (PRD §13, §32.2).
   *
   * A `signoff` is a formal human acceptance and is audited distinctly from a
   * `consequential` approval — so the queue must show which one it is rather
   * than presenting every gate as an undifferentiated "approve".
   *
   * Defaults to `consequential`: it is the safer assumption for a gate of
   * unknown class, since it still halts for a human.
   */
  capabilityClass: CapabilityClass.default("consequential"),
  /**
   * True when this checkpoint cannot be waived by the owning role or the
   * Project Admin fallback — the security and release sign-offs (PRD §44.5).
   */
  mandatory: z.boolean().default(false),
  title: z.string(),
  summary: z.string(),
  /** "agent" or a person's display name. */
  requestedBy: z.string(),
  requestedAt: Timestamp,
  /** ISO SLA deadline when one applies — powers the countdown. */
  deadline: Timestamp.nullish(),
  /** Approval gates only. */
  artifact: GateArtifactRef.nullish(),
  /** Clarification gates only. */
  question: z.string().nullish(),
});
export type ApprovalGate = z.infer<typeof ApprovalGate>;

/** Summary tiles for the inbox header. */
export const ApprovalQueueMetrics = z.object({
  approvals: z.number().int().nonnegative(),
  clarifications: z.number().int().nonnegative(),
  oldestMinutes: z.number().int().nonnegative(),
  generatedAt: Timestamp,
});
export type ApprovalQueueMetrics = z.infer<typeof ApprovalQueueMetrics>;

/** Response of a dummy decision/answer POST. */
