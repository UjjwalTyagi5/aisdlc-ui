"use client";

import * as React from "react";
import { Check, Loader2, MessageSquare, Pencil, RotateCcw, X } from "lucide-react";

import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { StatusBadge, type StatusKey } from "@/components/ui/status-badge";
import { Textarea } from "@/components/ui/textarea";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import type { ApprovalDecision, UserRef } from "@/lib/schemas";

export interface ApprovalCardProps {
  status: StatusKey;
  title?: string;
  description?: React.ReactNode;
  /** Show a decision row when a final decision already exists. */
  decidedBy?: UserRef;
  decidedAt?: string;
  reason?: string;

  /** Handlers — omit any one to hide its button. */
  onApprove?: () => void | Promise<void>;
  onReject?: (reason: string) => void | Promise<void>;
  onRetry?: () => void | Promise<void>;
  onEdit?: () => void;
  onAskAgent?: () => void;

  /** Disable all actions (e.g. while a mutation is in flight). */
  pending?: boolean;
  /** Which decision was just clicked — used to show a spinner in the right button. */
  pendingDecision?: ApprovalDecision | null;

  className?: string;
}

export function ApprovalCard({
  status,
  title = "Gate: Approval required",
  description,
  decidedBy,
  decidedAt,
  reason,
  onApprove,
  onReject,
  onRetry,
  onEdit,
  onAskAgent,
  pending,
  pendingDecision,
  className,
}: ApprovalCardProps) {
  const [rejectOpen, setRejectOpen] = React.useState(false);
  const [rejectReason, setRejectReason] = React.useState("");

  const terminal =
    status === "approved" ||
    status === "rejected" ||
    status === "failed" ||
    status === "merged";

  return (
    <section
      className={cn(
        // Northstar approval card: brand-orange border + gradient bg
        "space-y-3 rounded-[var(--radius)] border border-primary/35 bg-gradient-to-b from-primary/7 to-transparent p-4",
        className,
      )}
      aria-label="Approval"
    >
      {/* Eyebrow: brand dot + mono label */}
      <div className="flex items-center gap-2">
        <span
          className="size-2 rounded-full bg-brand-bright shadow-[0_0_10px_var(--color-brand-bright)]"
          aria-hidden
        />
        <span className="font-mono text-[10.5px] font-semibold uppercase tracking-[0.1em] text-brand-bright">
          Human approval
        </span>
      </div>

      <header className="flex items-start justify-between gap-3">
        <div className="space-y-1">
          <h3 className="font-display flex items-center gap-2 text-[14px] font-bold">
            {title}
            <StatusBadge status={status} />
          </h3>
          {description && (
            <p className="text-muted-foreground text-[12.5px] leading-relaxed">{description}</p>
          )}
        </div>
      </header>

      {terminal && decidedBy && (
        <div className="text-muted-foreground flex items-center gap-2 border-t border-line-soft pt-3 text-xs">
          <Avatar className="size-5">
            <AvatarFallback>{decidedBy.initials}</AvatarFallback>
          </Avatar>
          <span>
            {status === "approved" ? "Approved" : status === "rejected" ? "Rejected" : "Decided"}{" "}
            by {decidedBy.name}
            {decidedAt &&
              ` · ${new Date(decidedAt).toLocaleString(undefined, {
                dateStyle: "medium",
                timeStyle: "short",
              })}`}
          </span>
        </div>
      )}
      {reason && (
        <blockquote className="text-muted-foreground border-l-2 border-primary/40 pl-3 text-[12.5px]">
          &ldquo;{reason}&rdquo;
        </blockquote>
      )}

      {!terminal && (
        <>
          {rejectOpen ? (
            <div className="space-y-2">
              <Textarea
                autoFocus
                placeholder="Reason (required) — what should the agent fix?"
                value={rejectReason}
                onChange={(e) => setRejectReason(e.target.value)}
                rows={3}
                aria-label="Rejection reason"
              />
              <div className="flex justify-end gap-2">
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => {
                    setRejectOpen(false);
                    setRejectReason("");
                  }}
                  disabled={pending}
                >
                  Cancel
                </Button>
                <Button
                  variant="destructive"
                  size="sm"
                  onClick={() => onReject?.(rejectReason.trim())}
                  disabled={!rejectReason.trim() || pending}
                  aria-busy={pendingDecision === "reject"}
                >
                  {pendingDecision === "reject" ? (
                    <Loader2 className="size-4 animate-spin" />
                  ) : (
                    <X className="size-4" />
                  )}
                  Submit rejection
                </Button>
              </div>
            </div>
          ) : (
            <div className="flex flex-wrap gap-2 border-t border-line-soft pt-3">
              {onApprove && (
                <Button
                  onClick={onApprove}
                  size="sm"
                  disabled={pending}
                  aria-busy={pendingDecision === "approve"}
                  className="bg-gradient-to-r from-brand-gradient-from to-brand-gradient-to text-white"
                >
                  {pendingDecision === "approve" ? (
                    <Loader2 className="size-4 animate-spin" />
                  ) : (
                    <Check className="size-4" />
                  )}
                  Approve
                </Button>
              )}
              {onEdit && (
                <Button variant="outline" size="sm" onClick={onEdit} disabled={pending}>
                  <Pencil className="size-4" />
                  Edit
                </Button>
              )}
              {onReject && (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setRejectOpen(true)}
                  disabled={pending}
                  data-testid="approval-reject"
                  className="text-destructive hover:text-destructive"
                >
                  <X className="size-4" />
                  Reject
                </Button>
              )}
              {onRetry && (
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={onRetry}
                  disabled={pending}
                  aria-busy={pendingDecision === "retry"}
                >
                  {pendingDecision === "retry" ? (
                    <Loader2 className="size-4 animate-spin" />
                  ) : (
                    <RotateCcw className="size-4" />
                  )}
                  Retry
                </Button>
              )}
              {onAskAgent && (
                <Button
                  variant="ghost"
                  size="sm"
                  className="ml-auto"
                  onClick={onAskAgent}
                  disabled={pending}
                >
                  <MessageSquare className="size-4" />
                  Ask agent
                </Button>
              )}
            </div>
          )}
        </>
      )}
    </section>
  );
}
