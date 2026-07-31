import { z } from "zod";

import { api } from "./client";

const EvidenceJobStarted = z.object({ job_id: z.string() });
export type EvidenceJobStarted = z.infer<typeof EvidenceJobStarted>;

const EvidenceJobStatus = z.object({
  status: z.enum(["pending", "processing", "ready", "failed"]),
  download_url: z.string().optional(),
});
export type EvidenceJobStatus = z.infer<typeof EvidenceJobStatus>;

/** Trigger async evidence ZIP build for a run. Returns the polling job_id. */
export const requestEvidence = (runId: string) =>
  api(`/runs/${encodeURIComponent(runId)}/evidence`, {
    method: "POST",
    schema: EvidenceJobStarted,
  });

/** Poll the status of an in-progress evidence export job. */
export const pollEvidenceStatus = (runId: string, jobId: string) =>
  api(
    `/runs/${encodeURIComponent(runId)}/evidence/${encodeURIComponent(jobId)}/status`,
    { schema: EvidenceJobStatus },
  );
