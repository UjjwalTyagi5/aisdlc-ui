import { z } from "zod";

import {
  ApprovalEvent,
  type ApprovalSubmitInput,
  type ProjectId,
  Run,
  RunCreateResponse,
  type RunId,
  Step,
  paginated,
} from "@/lib/schemas";

import { api } from "./client";

export const createRun = (body: {
  project_id: string;
  /** Preferred: the exact provider connection + model (from /model/options). */
  offering_id?: string | null;
  /** Legacy fallback when no offering is chosen. */
  model_id?: string | null;
  trigger?: string;
  /**
   * Accepted and ignored. EVERY run is chat-driven now — the Copilot drives each
   * stage and gate approvals advance it via POST /api/runs/[id]/copilot/advance.
   * The field survives only so an older client does not fail schema validation
   * mid-deploy; the backend no longer branches on it.
   */
  conversational?: boolean;
}) => api("/runs", { method: "POST", body, schema: RunCreateResponse });

export const listRuns = (query?: {
  projectId?: ProjectId;
  status?: string;
  page?: number;
  pageSize?: number;
}) =>
  api("/runs", {
    query: query as Record<string, string | number | undefined>,
    schema: paginated(Run),
  });

export const getRun = (id: RunId) =>
  api(`/runs/${encodeURIComponent(id)}`, { schema: Run });

/** Cancel a running run. */
export const cancelRun = (id: RunId) =>
  api(`/runs/${encodeURIComponent(id)}/cancel`, { method: "POST", schema: Run });

/** Hard-delete a run (and its artifacts). */
export const deleteRun = (id: RunId) =>
  api(`/runs/${encodeURIComponent(id)}`, { method: "DELETE" });

export const getRunSteps = (id: RunId) =>
  api(`/runs/${encodeURIComponent(id)}/steps`, { schema: z.array(Step) });

export const submitApproval = (id: RunId, input: ApprovalSubmitInput) =>
  api(`/runs/${encodeURIComponent(id)}/approvals`, {
    method: "POST",
    body: input,
    schema: ApprovalEvent,
  });

/** Response from the conversational advance endpoint. */
export const CopilotAdvanceResult = z.object({
  current_stage: z.string().nullable(),
  status: z.string(),
});
export type CopilotAdvanceResult = z.infer<typeof CopilotAdvanceResult>;

/**
 * Resolve a run's gate — the Copilot's Approve/Reject, and the ONLY way a run
 * advances.
 *
 * It replaced the `hitl.decision` signal, which needed a workflow engine to
 * receive it. The server re-checks the stage's approve permission before any state
 * change, so this is not a thinner path than the one it replaced — it is the same
 * check without the engine.
 *
 * `reason` is free text and carries a clarification's ANSWER as well as a
 * rejection's explanation; both are recorded on the audit event.
 */
export const advanceCopilotRun = (
  id: RunId,
  input: { decision: "approved" | "rejected"; stage: string; reason?: string },
) =>
  api(`/runs/${encodeURIComponent(id)}/copilot/advance`, {
    method: "POST",
    body: input,
    schema: CopilotAdvanceResult,
  });
