import { z } from "zod";

import { ArtifactId, RunId, StepId } from "./ids";
import { Cost, Timestamp } from "./primitives";
import { Status } from "./enums";

/**
 * SSE event stream for a live run. Discriminated on `type` so clients
 * can dispatch narrowly. All events carry `at` for client-side ordering.
 */
export const RunStartedEvent = z.object({
  type: z.literal("run.started"),
  runId: RunId,
  at: Timestamp,
});

export const StepProgressEvent = z.object({
  type: z.literal("step.progress"),
  runId: RunId,
  stepId: StepId,
  title: z.string(),
  status: Status,
  progress: z.number().min(0).max(1).optional(),
  at: Timestamp,
});

export const StepOutputDeltaEvent = z.object({
  type: z.literal("step.output.delta"),
  runId: RunId,
  stepId: StepId,
  /** Streaming token/fragment. UI appends to a buffer. */
  delta: z.string(),
  at: Timestamp,
});

export const ArtifactUpdatedEvent = z.object({
  type: z.literal("artifact.updated"),
  runId: RunId,
  artifactId: ArtifactId,
  status: Status,
  /** Optional generated-document metadata (from a `file_generated` WS event) so
   *  the chat-produced document can be surfaced + downloaded on the main screen. */
  name: z.string().optional(),
  url: z.string().optional(),
  at: Timestamp,
});

export const HitlPendingEvent = z.object({
  type: z.literal("hitl.pending"),
  runId: RunId,
  artifactId: ArtifactId.nullable(),
  title: z.string(),
  at: Timestamp,
});

export const CostUpdateEvent = z.object({
  type: z.literal("cost.update"),
  runId: RunId,
  cost: Cost,
  at: Timestamp,
});

export const GuardrailHitEvent = z.object({
  type: z.literal("guardrail.hit"),
  runId: RunId,
  kind: z.enum(["pii", "prompt_injection", "policy", "budget"]),
  message: z.string(),
  blocked: z.boolean(),
  at: Timestamp,
});

export const RunCompletedEvent = z.object({
  type: z.literal("run.completed"),
  runId: RunId,
  status: Status,
  at: Timestamp,
});

export const StreamEvent = z.discriminatedUnion("type", [
  RunStartedEvent,
  StepProgressEvent,
  StepOutputDeltaEvent,
  ArtifactUpdatedEvent,
  HitlPendingEvent,
  CostUpdateEvent,
  GuardrailHitEvent,
  RunCompletedEvent,
]);
export type StreamEvent = z.infer<typeof StreamEvent>;
