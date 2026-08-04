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
import { ROLE_META, type PlatformRole } from "@/lib/roles";
import { REQUEST_TYPE_LABEL } from "@/lib/schemas/governance-approval";
import type { GovernanceApproval } from "@/lib/schemas";

/**
 * The role actually holding it — `currentApproverRole` when the request has
 * one, falling back to the type's default route.
 *
 * The fallback is not cosmetic: after an escalation the two disagree, and the
 * type map is the stale answer. A row that kept naming the original tier would
 * tell a BU Admin a request was still with the Project Admin it had already
 * climbed past.
 */
function approverRoleOf(approval: GovernanceApproval): PlatformRole {
  return (
    (approval.currentApproverRole as PlatformRole | null) ??
    GOVERNANCE_APPROVER_ROLE[approval.type]
  );
}

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
          {REQUEST_TYPE_LABEL[approval.type]}
        </span>
        <span className="text-brand-bright inline-flex items-center gap-1 font-mono text-[10px] uppercase tracking-[0.08em]">
          <ShieldCheck className="size-3" aria-hidden />
          Governance
        </span>
        <span className="text-foreground font-medium">{approval.workspaceName}</span>
        <span className="text-muted-foreground">
          Waiting on{" "}
          <span className="text-foreground font-medium">
            {ROLE_META[approverRoleOf(approval)].label}
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
