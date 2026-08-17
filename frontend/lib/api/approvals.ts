import { z } from "zod";

import { ApprovalGate, ApprovalQueueMetrics } from "@/lib/schemas";

import { api } from "./client";

export interface ApprovalFilters {
  /** "approval" | "clarification" — omit for all. */
  type?: string;
}

export const listApprovals = (filters: ApprovalFilters = {}) =>
  api("/approvals", {
    query: { type: filters.type || undefined },
    schema: z.array(ApprovalGate),
  });

export const getApprovalMetrics = () =>
  api("/approvals/metrics", { schema: ApprovalQueueMetrics });

// `decideGate` / `answerGate` lived here, posting to /api/approvals/{id}/decision —
// a route that acknowledged a decision and persisted nothing. Both were unused: the
// gate UI resolves through `advanceCopilotRun`, which actually moves the run and is
// permission-checked server-side. Removed rather than left as a working-looking
// alternative to the real one.
