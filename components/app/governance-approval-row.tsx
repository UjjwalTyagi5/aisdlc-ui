"use client";

import * as React from "react";
import Link from "next/link";
import { useMutation } from "@tanstack/react-query";
import { toast } from "sonner";
import { ExternalLink, ShieldCheck } from "lucide-react";
import { formatDistanceToNow } from "date-fns";

import { ApprovalCard } from "@/components/app/approval-card";
import { decideGovernanceApproval } from "@/lib/api/governance-approvals";
import { GOVERNANCE_APPROVER_ROLE } from "@/lib/governance";
import { ROLE_META } from "@/lib/roles";
import type { GovernanceApproval } from "@/lib/schemas";

const TYPE_LABEL: Record<GovernanceApproval["type"], string> = {
  project_creation: "Project creation",
  model_credential: "Model credential",
  budget_increase: "Budget increase",
  project_archive: "Project archive",
  agent_default_org: "Org agent default",
  agent_default_workspace: "Business Unit agent default",
  agent_default_project: "Project agent default",
};

/**
 * One pending governance approval — project creation or model-credential
 * onboarding, both routed to a Business Unit Admin. Mirrors the layout of
 * `ApprovalGateRow` (meta line + a single action card) but has no
 * phase/run/artifact concepts — see lib/schemas/governance-approval.ts for
 * why this is a separate type family sharing the same inbox.
 */
export function GovernanceApprovalRow({
  approval,
  onResolved,
}: {
  approval: GovernanceApproval;
  onResolved: (id: string) => void;
}) {
  const decide = useMutation({
    mutationFn: (input: { decision: "approve" | "reject"; reason?: string }) =>
      decideGovernanceApproval(approval.id, input),
    onSuccess: (_data, vars) => {
      toast.success(vars.decision === "approve" ? "Approved" : "Rejected");
      onResolved(approval.id);
    },
    onError: (err) =>
      toast.error("Couldn't submit decision", {
        description: err instanceof Error ? err.message : undefined,
      }),
  });

  const pendingDecision = decide.isPending ? (decide.variables?.decision ?? null) : null;
  const age = formatDistanceToNow(new Date(approval.requestedAt), { addSuffix: true });

  return (
    <li className="space-y-2">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 px-1 text-[11.5px]">
        <span className="border-line-soft text-muted-foreground rounded-full border px-2 py-0.5 font-mono text-[10px] uppercase tracking-[0.08em]">
          {TYPE_LABEL[approval.type]}
        </span>
        <span className="text-brand-bright inline-flex items-center gap-1 font-mono text-[10px] uppercase tracking-[0.08em]">
          <ShieldCheck className="size-3" aria-hidden />
          Governance
        </span>
        <span className="text-foreground font-medium">{approval.workspaceName}</span>
        <span className="text-muted-foreground">
          Waiting on{" "}
          <span className="text-foreground font-medium">
            {ROLE_META[GOVERNANCE_APPROVER_ROLE[approval.type]].label}
          </span>
        </span>
        <span className="text-muted-foreground">
          · {approval.requestedBy} · {age}
        </span>
        {approval.projectId && (
          <Link
            href={`/projects/${approval.projectId}`}
            className="text-brand-bright ml-auto inline-flex items-center gap-1 underline-offset-2 hover:underline"
          >
            Open project
            <ExternalLink className="size-3" aria-hidden />
          </Link>
        )}
      </div>

      <ApprovalCard
        status="awaiting_approval"
        title={approval.title}
        description={approval.summary}
        pending={decide.isPending}
        pendingDecision={pendingDecision}
        onApprove={() => decide.mutate({ decision: "approve" })}
        onReject={(reason) => decide.mutate({ decision: "reject", reason })}
      />
    </li>
  );
}
