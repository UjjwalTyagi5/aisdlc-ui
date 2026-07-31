import { z } from "zod";

import { api } from "./client";

/**
 * Schema matching the backend EvalRecordOut from shared/routers/_schemas.py.
 * camelCase field names match the Pydantic model's serialisation convention.
 */
export const EvalRecord = z.object({
  id: z.string().uuid(),
  tenantId: z.string().uuid(),
  runId: z.string().nullable(),
  agentType: z.string(),
  score: z.number().nullable(),
  signals: z.record(z.unknown()).nullable(),
  createdAt: z.string().datetime({ offset: true }),
});
export type EvalRecord = z.infer<typeof EvalRecord>;

/** Fetch all EvalRecords for a run (newest first). The frontend uses the first
 *  (latest) record as the primary quality signal. Requires artifact:view. */
export const getRunEval = (runId: string) =>
  api(`/runs/${encodeURIComponent(runId)}/eval`, {
    schema: z.array(EvalRecord),
  });
