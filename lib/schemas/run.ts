import { z } from "zod";

import { ProjectId, RunId, UserId } from "./ids";
import { AgentType, Phase, Status } from "./enums";
import { Cost, Timestamp } from "./primitives";

// "chat" — a lightweight run minted to home artifacts generated from an agent chat
// (see backend shared/services/chat_artifacts.py).
export const RunTrigger = z.enum(["manual", "webhook", "schedule", "agent", "chat"]);
export type RunTrigger = z.infer<typeof RunTrigger>;

export const RunCreateResponse = z.object({
  runId: z.string(),
  temporalWorkflowId: z.string(),
});
export type RunCreateResponse = z.infer<typeof RunCreateResponse>;

export const Run = z.object({
  id: RunId,
  projectId: ProjectId,
  title: z.string().min(1).max(200),
  agent: AgentType,
  phase: Phase,
  status: Status,
  trigger: RunTrigger,
  startedBy: UserId.nullable(),
  startedAt: Timestamp,
  completedAt: Timestamp.nullable(),
  durationMs: z.number().int().nonnegative().nullable(),
  cost: Cost,
  /** For awaiting_approval runs — which user role(s) are allowed to decide. */
  pendingApprovers: z.array(z.enum(["admin", "member"])).optional(),
  /** For awaiting_clarification runs — the agent's question(s) (REQ-M10-06). */
  clarificationQuestions: z.array(z.string()).optional(),
  /** For awaiting_clarification runs — the SLA deadline for answering. */
  clarificationDeadline: Timestamp.optional(),
});
export type Run = z.infer<typeof Run>;
