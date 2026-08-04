"use client";

import * as React from "react";
import Link from "next/link";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { ArrowUpCircle, ExternalLink, Loader2, Paperclip } from "lucide-react";
import { format } from "date-fns";

import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Textarea } from "@/components/ui/textarea";
import { RequestTimeline } from "@/components/requests/request-timeline";
import {
  RequestPriorityBadge,
  RequestStatusBadge,
} from "@/components/requests/request-status-badge";
import { useAccessScope } from "@/hooks/use-access-scope";
import {
  cancelRequest,
  decideGovernanceApproval,
  escalateRequest,
} from "@/lib/api/governance-approvals";
import { qk } from "@/lib/api/query-keys";
import { canDecideRequest, canEscalate } from "@/lib/requests/routing";
import { ROLE_META, type PlatformRole } from "@/lib/roles";
import { REQUEST_TYPE_LABEL } from "@/lib/schemas/governance-approval";
import type { GovernanceApproval } from "@/lib/schemas";

function fileSize(bytes: number): string {
  if (bytes >= 1_048_576) return `${(bytes / 1_048_576).toFixed(1)} MB`;
  if (bytes >= 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${bytes} B`;
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="space-y-0.5">
      <p className="text-muted-foreground font-mono text-[10px] tracking-[0.12em] uppercase">
        {label}
      </p>
      <div className="text-[12.5px]">{children}</div>
    </div>
  );
}

/**
 * One request, in full: the packet, the trail, and whatever the viewer may do
 * about it.
 *
 * WHY THE ACTIONS ARE THREE-WAY. Approve and reject are the approver's;
 * withdraw is the initiator's; escalate is the approver's admission that they
 * cannot decide it. They are never all available at once, and the reason a
 * button is missing is stated rather than left to inference — a disabled
 * control with no cause sends people looking for a permission they may already
 * hold (see `canDecideRequest`, which returns the reason for exactly this).
 */
export function RequestDetailSheet({
  request,
  open,
  onOpenChange,
}: {
  request: GovernanceApproval | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const queryClient = useQueryClient();
  const { role, scope } = useAccessScope();
  const [reason, setReason] = React.useState("");

  React.useEffect(() => {
    if (!open) setReason("");
  }, [open, request?.id]);

  const invalidate = React.useCallback(() => {
    queryClient.invalidateQueries({ queryKey: qk.governanceApprovals.list() });
  }, [queryClient]);

  const decide = useMutation({
    mutationFn: (decision: "approve" | "reject") =>
      decideGovernanceApproval(request!.id, {
        decision,
        reason: reason.trim() || undefined,
      }),
    onSuccess: (_d, decision) => {
      toast.success(decision === "approve" ? "Request approved" : "Request rejected");
      invalidate();
      onOpenChange(false);
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : "Couldn't record the decision"),
  });

  const escalate = useMutation({
    mutationFn: () => escalateRequest(request!.id, reason.trim() || undefined),
    onSuccess: (updated) => {
      toast.success(
        updated.currentApproverRole
          ? `Escalated to the ${ROLE_META[updated.currentApproverRole as PlatformRole].label}`
          : "Escalated",
      );
      invalidate();
      onOpenChange(false);
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : "Couldn't escalate"),
  });

  const withdraw = useMutation({
    mutationFn: () => cancelRequest(request!.id, reason.trim() || undefined),
    onSuccess: () => {
      toast.success("Request withdrawn");
      invalidate();
      onOpenChange(false);
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : "Couldn't withdraw"),
  });

  if (!request) return null;

  const approverRole = (request.currentApproverRole as PlatformRole | null) ?? null;
  const eligibility = canDecideRequest({
    currentApproverRole: approverRole,
    requestedById: request.requestedById,
    status: request.status,
    viewerRole: role,
    viewerIdentityId: scope?.identityId ?? null,
  });
  const isMine =
    !!request.requestedById && request.requestedById === (scope?.identityId ?? null);
  const isOpen =
    request.status === "submitted" ||
    request.status === "pending_review" ||
    request.status === "escalated";
  const busy = decide.isPending || escalate.isPending || withdraw.isPending;

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="flex w-full flex-col gap-0 overflow-y-auto p-0 sm:max-w-[560px]">
        <SheetHeader className="border-line-soft space-y-2 border-b px-5 py-4 text-left">
          <div className="flex flex-wrap items-center gap-2">
            <RequestStatusBadge status={request.status} />
            <RequestPriorityBadge priority={request.priority} />
            <span className="border-line-soft text-muted-foreground rounded-full border px-2 py-0.5 font-mono text-[10px] tracking-[0.08em] uppercase">
              {REQUEST_TYPE_LABEL[request.type]}
            </span>
          </div>
          <SheetTitle className="text-[16px] leading-snug">{request.title}</SheetTitle>
          <SheetDescription className="text-[12.5px]">{request.summary}</SheetDescription>
        </SheetHeader>

        <div className="flex-1 space-y-5 px-5 py-4">
          {request.description && (
            <p className="text-[12.5px] leading-relaxed">{request.description}</p>
          )}

          <div className="grid grid-cols-2 gap-4">
            <Field label="Raised by">
              {request.requestedBy}
              {request.requestedByRole && (
                <span className="text-muted-foreground">
                  {" "}
                  ({ROLE_META[request.requestedByRole as PlatformRole]?.label})
                </span>
              )}
            </Field>
            <Field label="Raised">
              {format(new Date(request.requestedAt), "d MMM yyyy, HH:mm")}
            </Field>
            <Field label="Business unit">{request.workspaceName}</Field>
            <Field label="Project">
              {request.projectId ? (
                <Link
                  href={`/projects/${request.projectId}`}
                  className="text-brand-bright inline-flex items-center gap-1 underline-offset-2 hover:underline"
                >
                  {request.projectName}
                  <ExternalLink className="size-3" aria-hidden />
                </Link>
              ) : (
                <span className="text-muted-foreground">Not project-specific</span>
              )}
            </Field>
            <Field label="Current approver">
              {approverRole ? (
                ROLE_META[approverRole].label
              ) : (
                <span className="text-muted-foreground">
                  {isOpen ? "Unassigned" : "Closed"}
                </span>
              )}
            </Field>
            <Field label="Escalations">
              {request.escalationCount === 0 ? (
                <span className="text-muted-foreground">None</span>
              ) : (
                `${request.escalationCount} tier${request.escalationCount === 1 ? "" : "s"}`
              )}
            </Field>
          </div>

          {request.attachments.length > 0 && (
            <div className="space-y-1.5">
              <p className="text-muted-foreground font-mono text-[10px] tracking-[0.12em] uppercase">
                Attachments
              </p>
              <ul className="space-y-1">
                {request.attachments.map((a) => (
                  <li
                    key={a.name}
                    className="border-line-soft bg-surface-1 flex items-center gap-2 rounded-md border px-2.5 py-1.5 text-[12px]"
                  >
                    <Paperclip className="text-muted-foreground size-3 shrink-0" aria-hidden />
                    <span className="min-w-0 flex-1 truncate">{a.name}</span>
                    <span className="text-muted-foreground shrink-0 font-mono text-[10.5px]">
                      {fileSize(a.sizeBytes)}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          <div className="space-y-2">
            <p className="text-muted-foreground font-mono text-[10px] tracking-[0.12em] uppercase">
              Approval timeline
            </p>
            <RequestTimeline events={request.timeline} />
          </div>
        </div>

        {/* ── Actions ─────────────────────────────────────────────────────── */}
        {isOpen && (eligibility.allowed || isMine) && (
          <div className="border-line-soft bg-panel-elevated space-y-2.5 border-t px-5 py-4">
            <Textarea
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              rows={2}
              placeholder={
                eligibility.allowed
                  ? "Reason — required when rejecting, recorded either way"
                  : "Reason for withdrawing (optional)"
              }
              className="border-line-soft text-[12.5px]"
            />
            <div className="flex flex-wrap gap-2">
              {eligibility.allowed && (
                <>
                  <Button size="sm" disabled={busy} onClick={() => decide.mutate("approve")}>
                    {decide.isPending && decide.variables === "approve" && (
                      <Loader2 className="size-3.5 animate-spin" aria-hidden />
                    )}
                    Approve
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    className="border-destructive/40 text-destructive hover:bg-destructive/5"
                    disabled={busy || !reason.trim()}
                    title={!reason.trim() ? "A rejection needs a reason" : undefined}
                    onClick={() => decide.mutate("reject")}
                  >
                    Reject
                  </Button>
                  {canEscalate(approverRole) && (
                    <Button
                      size="sm"
                      variant="outline"
                      className="border-line-soft gap-1.5"
                      disabled={busy}
                      onClick={() => escalate.mutate()}
                    >
                      <ArrowUpCircle className="size-3.5" aria-hidden />
                      Escalate
                    </Button>
                  )}
                </>
              )}
              {isMine && (
                <Button
                  size="sm"
                  variant="ghost"
                  className="text-muted-foreground ml-auto"
                  disabled={busy}
                  onClick={() => withdraw.mutate()}
                >
                  Withdraw
                </Button>
              )}
            </div>
          </div>
        )}

        {/* Why you can't act, stated rather than left to a missing button. */}
        {isOpen && !eligibility.allowed && !isMine && eligibility.reason && (
          <div className="border-line-soft text-muted-foreground border-t px-5 py-3 text-[12px]">
            {eligibility.reason}
          </div>
        )}

        {!isOpen && request.decidedBy && (
          <div
            className={cn(
              "border-line-soft border-t px-5 py-3 text-[12px]",
              request.status === "approved" ? "text-success" : "text-muted-foreground",
            )}
          >
            {request.status === "approved" ? "Approved" : "Rejected"} by {request.decidedBy}
            {request.decidedAt &&
              ` on ${format(new Date(request.decidedAt), "d MMM yyyy, HH:mm")}`}
            {request.reason && ` — ${request.reason}`}
          </div>
        )}
      </SheetContent>
    </Sheet>
  );
}
