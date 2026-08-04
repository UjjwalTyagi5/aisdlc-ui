import { z } from "zod";

import {
  GovernanceApproval,
  type GovernanceApprovalDecisionInput,
  type RequestCreateInput,
} from "@/lib/schemas/governance-approval";

import { api } from "./client";

export const listGovernanceApprovals = (workspaceId?: string) =>
  api("/governance-approvals", {
    query: { workspaceId: workspaceId || undefined },
    schema: z.array(GovernanceApproval),
  });

export const decideGovernanceApproval = (id: string, input: GovernanceApprovalDecisionInput) =>
  api(`/governance-approvals/${encodeURIComponent(id)}/decide`, {
    method: "POST",
    body: input,
    schema: GovernanceApproval,
  });

/** Raise a request. The approver is derived server-side, never sent. */
export const createRequest = (input: RequestCreateInput) =>
  api("/governance-approvals", {
    method: "POST",
    body: input,
    schema: GovernanceApproval,
  });

/** Withdraw your own open request. */
export const cancelRequest = (id: string, reason?: string) =>
  api(`/governance-approvals/${encodeURIComponent(id)}/cancel`, {
    method: "POST",
    body: { reason },
    schema: GovernanceApproval,
  });

/** Send it one tier up the chain. */
export const escalateRequest = (id: string, reason?: string) =>
  api(`/governance-approvals/${encodeURIComponent(id)}/escalate`, {
    method: "POST",
    body: { reason },
    schema: GovernanceApproval,
  });
