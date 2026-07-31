import { z } from "zod";

import { ApprovalGate, ApprovalQueueMetrics, GateDecisionResult } from "@/lib/schemas";
import type { ApprovalDecision } from "@/lib/schemas";

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

/** Submit an approval decision for a gate (dummy ack today). */
export const decideGate = (input: { id: string; decision: ApprovalDecision; reason?: string }) =>
  api(`/approvals/${encodeURIComponent(input.id)}/decision`, {
    method: "POST",
    body: { decision: input.decision, reason: input.reason },
    schema: GateDecisionResult,
  });

/** Answer an agent clarification gate (dummy ack today). */
export const answerGate = (input: { id: string; answer: string }) =>
  api(`/approvals/${encodeURIComponent(input.id)}/decision`, {
    method: "POST",
    body: { answer: input.answer },
    schema: GateDecisionResult,
  });
