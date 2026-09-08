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
import { advanceCopilotRun } from "@/lib/api/runs";
import { approveArtifact, rejectArtifact } from "@/lib/api/artifacts";
import { PHASE_LABEL } from "@/lib/agents";
import { CAPABILITY_CLASS_META } from "@/lib/capability-class";
import type { ApprovalDecision, ApprovalGate } from "@/lib/schemas";

/** One pending gate: a meta line + the matching action card. No nested cards —
 *  the meta sits on the page surface above the (single) card. */
export function ApprovalGateRow({
  gate,
  onResolved,
}: {
  gate: ApprovalGate;
  onResolved: (id: string) => void;
}) {
  // Both actions below resolve the gate through the SAME endpoint, because there is
  // only one way a run advances now. It replaced a `hitl.decision` signal that
  // needed a workflow engine to receive it; the server still re-checks the stage's
  // approve permission before touching any state.
  //
  // `artifactId` and `idempotencyKey` are gone with the signal. The gate is
  // identified by the run and its stage — the server resolves the artifact itself —
  // and replay safety comes from the gate being closed after the first decision
  // rather than from a key the client invents.
  const decide = useMutation({
    // `void`, because the two branches resolve to different shapes (an artifact row
    // versus a run decision) and neither result is read — `onSuccess` only needs to
    // know that it worked.
    mutationFn: async (input: { decision: ApprovalDecision; reason?: string }): Promise<void> => {
      // A DOCUMENT IS NOT A RUN. Its decision lives on the artifact row
      // (`POST /artifacts/{id}/approve`), not on a run's gate, and it may have no run
      // at all — an uploaded document never had one. Routing it through
      // `advanceCopilotRun` would send `null` as a run id and fail at the URL.
      if (gate.type === "document") {
        const artifactId = gate.artifact?.id;
        if (!artifactId) throw new Error("This document gate has no artifact to decide.");
        if (input.decision === "approve") await approveArtifact(artifactId);
        else await rejectArtifact(artifactId, input.reason);
        return;
      }
      // `gate.phase` is non-null for every run gate; the schema only admits null for
      // the document case handled above.
      await advanceCopilotRun(gate.runId!, {
        decision: input.decision === "approve" ? "approved" : "rejected",
        stage: gate.phase ?? undefined,
        reason: input.reason,
      });
    },
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

  // A clarification is answered by advancing the gate with the answer as the
  // reason — the endpoint records it on the audit event and lets the stage carry on.
  const answer = useMutation({
    mutationFn: (text: string) =>
      // Clarifications are raised by a run, so both are present here.
      advanceCopilotRun(gate.runId!, {
        decision: "approved",
        stage: gate.phase ?? undefined,
        reason: text,
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
      {gate.type === "approval" || gate.type === "document" ? (
        <ApprovalCard
          status="awaiting_approval"
          title={gate.title}
          description={gate.summary}
          pending={decide.isPending}
          pendingDecision={pendingDecision}
          onApprove={() => decide.mutate({ decision: "approve" })}
          onReject={(reason) => decide.mutate({ decision: "reject", reason })}
          // NO RETRY ON A DOCUMENT. Retry re-runs the stage that produced the
          // artifact; a document somebody uploaded has no agent to send it back to,
          // and offering the button would promise an action with nothing behind it.
          onRetry={
            gate.type === "document"
              ? undefined
              : () => decide.mutate({ decision: "retry" })
          }
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
        {/* A project-wide document belongs to no stage, so there is no phase to
            label it with — and "Requirements" would be a lie about which agent owns
            it. */}
        {gate.phase ? PHASE_LABEL[gate.phase] : "Project-wide"}
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
