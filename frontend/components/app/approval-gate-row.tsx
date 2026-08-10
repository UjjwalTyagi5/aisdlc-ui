"use client";

import * as React from "react";
import Link from "next/link";
import { useMutation } from "@tanstack/react-query";
import { toast } from "sonner";
import { BellRing, ExternalLink, FileText, ShieldAlert } from "lucide-react";
import { formatDistanceToNow } from "date-fns";

import { cn } from "@/lib/utils";
import { ApprovalCard } from "@/components/app/approval-card";
import { ClarificationCard } from "@/components/app/clarification-card";
import { sendApprovalSignal, sendClarificationAnswer } from "@/lib/api/signals";
import { PHASE_LABEL } from "@/lib/agents";
import { CAPABILITY_CLASS_META } from "@/lib/capability-class";
import type { ApprovalDecision, ApprovalGate, ArtifactId } from "@/lib/schemas";

/** One pending gate: a meta line + the matching action card. No nested cards —
 *  the meta sits on the page surface above the (single) card. */
export function ApprovalGateRow({
  gate,
  onResolved,
}: {
  gate: ApprovalGate;
  onResolved: (id: string) => void;
}) {
  const decide = useMutation({
    mutationFn: (input: { decision: ApprovalDecision; reason?: string }) =>
      sendApprovalSignal({
        runId: gate.runId,
        artifactId: (gate.artifact?.id ?? gate.runId) as ArtifactId,
        decision: input.decision,
        reason: input.reason,
        idempotencyKey: crypto.randomUUID(),
      }),
    onSuccess: (_data, vars) => {
      toast.success(
        vars.decision === "approve"
          ? "Approved"
          : vars.decision === "reject"
            ? "Rejected — sent back to the agent"
            : "Retry requested",
      );
      onResolved(gate.id);
    },
    onError: (err) =>
      toast.error("Couldn't submit decision", {
        description: err instanceof Error ? err.message : undefined,
      }),
  });

  const answer = useMutation({
    mutationFn: (text: string) =>
      sendClarificationAnswer({
        runId: gate.runId,
        clarificationId: gate.id,
        answer: text,
        idempotencyKey: crypto.randomUUID(),
      }),
    onSuccess: () => {
      toast.success("Answer sent to the agent");
      onResolved(gate.id);
    },
    onError: (err) =>
      toast.error("Couldn't send answer", {
        description: err instanceof Error ? err.message : undefined,
      }),
  });

  const pendingDecision: ApprovalDecision | null = decide.isPending
    ? (decide.variables?.decision ?? null)
    : null;

  return (
    <li className="space-y-2">
      <GateMeta gate={gate} />
      {gate.type === "approval" ? (
        <ApprovalCard
          status="awaiting_approval"
          title={gate.title}
          description={gate.summary}
          pending={decide.isPending}
          pendingDecision={pendingDecision}
          onApprove={() => decide.mutate({ decision: "approve" })}
          onReject={(reason) => decide.mutate({ decision: "reject", reason })}
          onRetry={() => decide.mutate({ decision: "retry" })}
        />
      ) : gate.type === "clarification" ? (
        <ClarificationCard
          questions={gate.question ? [gate.question] : []}
          clarificationId={gate.id}
          deadline={gate.deadline ?? undefined}
          canAnswer
          pending={answer.isPending}
          onSubmit={(text) => answer.mutate(text)}
        />
      ) : (
        // `outcome` — the result of something you raised (PRD §33.2). It is
        // informational: there is nothing to decide, so no decision controls.
        <div className="border-line-soft bg-panel-elevated rounded-xl border px-4 py-3">
          <div className="flex items-start gap-2.5">
            <BellRing className="text-info mt-0.5 size-4 shrink-0" aria-hidden />
            <div className="min-w-0">
              <p className="text-[13px] font-medium">{gate.title}</p>
              <p className="text-muted-foreground mt-1 text-[12.5px] leading-relaxed">
                {gate.summary}
              </p>
            </div>
            <button
              type="button"
              onClick={() => onResolved(gate.id)}
              className="text-muted-foreground hover:text-foreground ml-auto shrink-0 font-mono text-[11px] transition-colors"
            >
              Dismiss
            </button>
          </div>
        </div>
      )}
    </li>
  );
}

function GateMeta({ gate }: { gate: ApprovalGate }) {
  const age = formatDistanceToNow(new Date(gate.requestedAt), { addSuffix: true });
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 px-1 text-[11.5px]">
      <span
        className={cn(
          "rounded-full border px-2 py-0.5 font-mono text-[10px] uppercase tracking-[0.08em]",
          "border-line-soft text-muted-foreground",
        )}
      >
        {PHASE_LABEL[gate.phase]}
      </span>

      {/* Which class is being decided — a Sign-off is audited distinctly from
          a Consequential approval (PRD §13), so the queue must say which. */}
      {gate.type !== "outcome" && (
        <span
          className={cn(
            "rounded-full border px-2 py-0.5 font-mono text-[10px] tracking-[0.08em] uppercase",
            CAPABILITY_CLASS_META[gate.capabilityClass].chipClass,
          )}
          title={CAPABILITY_CLASS_META[gate.capabilityClass].meaning}
        >
          {CAPABILITY_CLASS_META[gate.capabilityClass].label}
        </span>
      )}

      {gate.mandatory && (
        <span
          className="text-destructive inline-flex items-center gap-1 font-mono text-[10px] tracking-[0.08em] uppercase"
          title="A mandatory checkpoint — it cannot be waived by the owning role or the Project Admin fallback."
        >
          <ShieldAlert className="size-3" aria-hidden />
          Mandatory
        </span>
      )}

      <span className="text-foreground font-medium">{gate.projectName}</span>
      <span className="text-muted-foreground">
        Waiting on <span className="text-foreground font-medium">{gate.waitingForRole}</span>
      </span>
      <span className="text-muted-foreground">· {gate.requestedBy} · {age}</span>
      {gate.artifact && (
        <span className="text-muted-foreground inline-flex items-center gap-1">
          <FileText className="size-3" aria-hidden />
          {gate.artifact.title}
        </span>
      )}
      <Link
        href={`/runs/${gate.runId}`}
        className="text-brand-bright ml-auto inline-flex items-center gap-1 underline-offset-2 hover:underline"
      >
        Open run
        <ExternalLink className="size-3" aria-hidden />
      </Link>
    </div>
  );
}
