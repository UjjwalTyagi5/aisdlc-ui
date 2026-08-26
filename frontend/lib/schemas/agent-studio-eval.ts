import { z } from "zod";

/**
 * A PASS/FAIL evaluation record for one specific Agent Studio draft version
 * (sub-project 4, evaluation-gated promotion). Mirrors the backend's
 * agent_default_evaluations row shape 1:1, snake_case on the wire.
 */
export const EvaluationResult = z.object({
  id: z.string(),
  target_type: z.enum(["profile", "skill"]),
  target_id: z.string(),
  agent_id: z.string(),
  scope: z.string(),
  result: z.enum(["pass", "fail"]),
  score: z.number(),
  signals: z.record(z.string(), z.unknown()),
  evaluator_id: z.string(),
  evaluator_role: z.string().nullable(),
  created_at: z.string().nullable(),
});
export type EvaluationResult = z.infer<typeof EvaluationResult>;
