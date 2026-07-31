import { z } from "zod";

import { GovernanceApproval, type GovernanceApprovalDecisionInput } from "@/lib/schemas/governance-approval";

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
