"use client";

import * as React from "react";
import { Clock, Send } from "lucide-react";

import { cn } from "@/lib/utils";
import { ApprovalCard } from "@/components/app/approval-card";
import { ownerRoleLabel, stageLabel } from "@/lib/copilot/stages";
import type { GateState } from "@/lib/copilot/types";
import type { ApprovalDecision } from "@/lib/schemas";

export interface GateInlineProps {
  runId: string;
  gate: GateState;
  /** Approve/reject over the Copilot WS so the advance reflects live (rail + chat). */
  onDecision?: (decision: "approved" | "rejected", stage: string, reason?: string) => void;
  className?: string;
}

/**
 * Inline pipeline gate with TWO states derived from `gate`:
 *
 *  - `can_approve === true`  → an interactive ApprovalCard whose Approve/Reject
 *    fire `sendApprovalSignal({ runId, artifactId: runId, decision, reason?,
 *    idempotencyKey })` — mirroring `run-detail-drawer.tsx` exactly (runId is the
 *    correlation key; the server maps hitl.decision → the right artifact via
 *    run_id + current_stage, D-08). The backend re-checks the phase permission,
 *    so this is UX gating on top of an authoritative server boundary.
 *
 *  - `can_approve === false` → a prominent, read-only "awaiting approval from
 *    {ownerRole}" panel with NO approve control (separation of duties; the server
 *    would 403 a forged approve anyway).
 */
export function GateInline({ gate, onDecision, className }: GateInlineProps) {
  const [pending, setPending] = React.useState(false);
  const [pendingDecision, setPendingDecision] =
    React.useState<ApprovalDecision | null>(null);

  const decide = React.useCallback(
    (decision: ApprovalDecision, reason?: string) => {
      // Send over the Copilot WS: the server advances current_stage and emits
      // stage.changed + a note on the same socket, so the rail and chat update live
      // (a REST advance mutated the DB but the live session never saw it).
      setPending(true);
      setPendingDecision(decision);
      onDecision?.(decision === "approve" ? "approved" : "rejected", gate.stage, reason);
    },
    [gate.stage, onDecision],
  );

  const owner = ownerRoleLabel(gate.owner_role);
  const label = stageLabel(gate.stage);

  if (gate.can_approve) {
    return (
      <ApprovalCard
        status="awaiting_approval"
        title={`Gate: ${label} sign-off`}
        description={
          <span>
            This stage is paused at an approval gate. You hold the{" "}
            <span className="font-mono text-xs">{owner}</span> permission — approve to
            advance the pipeline, or reject with feedback to re-run.
          </span>
        }
        onApprove={() => decide("approve")}
        onReject={(reason) => decide("reject", reason)}
        pending={pending}
        pendingDecision={pendingDecision}
        className={cn("border-warning/30 bg-warning/5", className)}
      />
    );
  }

  // Read-only cross-role state — no approve control.
  return (
    <section
      aria-label={`Awaiting approval from ${owner}`}
      className={cn(
        "space-y-3 rounded-[var(--radius)] border border-warning/35 bg-gradient-to-b from-warning/[0.08] to-transparent p-4",
        className,
      )}
    >
      <div className="flex items-center gap-2">
        <span
          className="bg-warning size-2 animate-pulse rounded-full shadow-[0_0_10px_var(--color-warning)]"
          aria-hidden
        />
        <span className="text-warning font-mono text-[10.5px] font-semibold uppercase tracking-[0.1em]">
          Awaiting approval
        </span>
      </div>

      <div className="flex items-start gap-3">
        <span className="border-warning/40 bg-warning/10 text-warning mt-0.5 flex size-9 shrink-0 items-center justify-center rounded-full border">
          <Clock className="size-4" aria-hidden />
        </span>
        <div className="min-w-0 space-y-1">
          <h3 className="font-display text-[14px] font-bold leading-tight">
            {label} — awaiting {owner}
          </h3>
          <p className="text-muted-foreground text-[12.5px] leading-relaxed">
            This gate is owned by the <span className="text-foreground font-medium">{owner}</span>{" "}
            (separation of duties — you can&apos;t approve your own work). It has been routed to their
            Approvals page. The pipeline resumes here automatically once they decide.
          </p>
        </div>
      </div>

      <div className="text-muted-foreground flex items-center gap-1.5 border-t border-warning/25 pt-3 font-mono text-[11px]">
        <Send className="size-3" aria-hidden />
        Notification sent to {owner}
      </div>
    </section>
  );
}
