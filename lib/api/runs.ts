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
   * Chat-driven run (Orchestrator Copilot): created WITHOUT Temporal — the Copilot
   * drives each stage conversationally and gate approvals advance it via
   * POST /api/runs/[id]/copilot/advance.
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

/** Cancel a running run — terminates its Temporal workflow and marks it cancelled. */
export const cancelRun = (id: RunId) =>
  api(`/runs/${encodeURIComponent(id)}/cancel`, { method: "POST", schema: Run });

/** Hard-delete a run (and its artifacts). */
export const deleteRun = (id: RunId) =>
  api(`/runs/${encodeURIComponent(id)}`, { method: "DELETE" });

/** Whether a Temporal worker is polling the queue (UI warns when none is). */
export const WorkerStatus = z.object({
  worker_available: z.boolean().nullable(),
  pollers: z.number().default(0),
  reason: z.string().optional(),
});
export type WorkerStatus = z.infer<typeof WorkerStatus>;

export const getWorkerStatus = () =>
  api(`/runs/worker-status`, { schema: WorkerStatus });

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
 * Advance (or re-run) a CONVERSATIONAL run at a gate — the Copilot's Approve/Reject.
 * Hits the signed BFF proxy `/api/runs/[id]/copilot/advance` (NOT the Temporal
 * `hitl.decision` signal — a conversational run has no workflow). The server
 * re-checks the stage approve permission.
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
