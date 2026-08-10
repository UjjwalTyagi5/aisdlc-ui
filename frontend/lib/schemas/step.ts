import { z } from "zod";

import { RunId, StepId } from "./ids";
import { AgentType, LlmProvider, Status } from "./enums";
import { Cost, Timestamp } from "./primitives";

export const StepKind = z.enum([
  "plan",
  "tool_call",
  "llm_call",
  "guardrail_check",
  "artifact_write",
  "hitl_wait",
]);
export type StepKind = z.infer<typeof StepKind>;

/**
 * One agent/tool/LLM call. Step keys are
 *   hash(inputs + prompt_version + model_version)
 * so retries are idempotent (per `DEVELOPMENT_PLAN_MVP.md` §5).
 */
export const Step = z.object({
  id: StepId,
  runId: RunId,
  index: z.number().int().nonnegative(),
  kind: StepKind,
  agent: AgentType,
  title: z.string(),
  status: Status,
  startedAt: Timestamp,
  completedAt: Timestamp.nullable(),
  durationMs: z.number().int().nonnegative().nullable(),
  cost: Cost.nullish(),
  /** Model snapshot (for replay + audit). */
  model: z
    .object({
      provider: LlmProvider,
      id: z.string(),
      version: z.string().optional(),
      promptVersion: z.string().optional(),
    })
    .nullish(),
  /** Short one-liner for the timeline. Full I/O lives in artifact records. */
  summary: z.string().nullish(),
  error: z
    .object({
      code: z.string(),
      message: z.string(),
    })
    .nullish(),
});
export type Step = z.infer<typeof Step>;
