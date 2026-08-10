"use client";

import * as React from "react";

import { cn } from "@/lib/utils";
import type {
  GovernanceApprovalStatus,
  RequestPriority,
} from "@/lib/schemas/governance-approval";

/**
 * Status colour is reserved for state, and these are states.
 *
 * `escalated` takes the warning tint rather than a neutral one on purpose: a
 * request that climbed is not simply "still open", it is open *because nobody
 * below answered*, and colouring it the same as `pending_review` would hide the
 * one fact an escalation exists to surface.
 *
 * `draft` and `cancelled` stay grey — neither is waiting on anyone, so neither
 * should compete for attention with the rows that are.
 */
const STATUS_META: Record<GovernanceApprovalStatus, { label: string; className: string }> = {
  draft: {
    label: "Draft",
    className: "border-line-soft bg-surface-1 text-muted-foreground",
  },
  submitted: {
    label: "Submitted",
    className: "border-info/40 bg-info/10 text-info",
  },
  pending_review: {
    label: "Pending review",
    className: "border-warning/40 bg-warning/10 text-warning",
  },
  escalated: {
    label: "Escalated",
    className: "border-warning/50 bg-warning/15 text-warning",
  },
  approved: {
    label: "Approved",
    className: "border-success/40 bg-success/10 text-success",
  },
  rejected: {
    label: "Rejected",
    className: "border-destructive/40 bg-destructive/10 text-destructive",
  },
  cancelled: {
    label: "Withdrawn",
    className: "border-line-soft bg-surface-1 text-muted-foreground",
  },
};

export function RequestStatusBadge({
  status,
  className,
}: {
  status: GovernanceApprovalStatus;
  className?: string;
}) {
  const meta = STATUS_META[status];
  return (
    <span
      className={cn(
        "inline-flex shrink-0 items-center rounded-full border px-2 py-0.5 font-mono text-[10px] tracking-[0.08em] uppercase",
        meta.className,
        className,
      )}
    >
      {meta.label}
    </span>
  );
}

export function requestStatusLabel(status: GovernanceApprovalStatus): string {
  return STATUS_META[status].label;
}

/**
 * Priority, shown only when it is above normal.
 *
 * A "Normal" chip on most rows would be pure noise — priority earns its space
 * precisely when it is not the default, and rendering it always would train
 * people to stop reading it.
 */
const PRIORITY_META: Record<RequestPriority, { label: string; className: string } | null> = {
  low: null,
  normal: null,
  high: { label: "High", className: "border-warning/40 text-warning" },
  urgent: { label: "Urgent", className: "border-destructive/40 text-destructive" },
};

export function RequestPriorityBadge({ priority }: { priority: RequestPriority }) {
  const meta = PRIORITY_META[priority];
  if (!meta) return null;
  return (
    <span
      className={cn(
        "inline-flex shrink-0 items-center rounded-full border px-1.5 py-px font-mono text-[9.5px] tracking-[0.08em] uppercase",
        meta.className,
      )}
    >
      {meta.label}
    </span>
  );
}
