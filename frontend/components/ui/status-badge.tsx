import * as React from "react";
import {
  AlertCircle,
  CheckCircle2,
  CircleDashed,
  CircleDot,
  GitMerge,
  GitPullRequest,
  Loader2,
  Pause,
  XCircle,
} from "lucide-react";

import { cn } from "@/lib/utils";

/**
 * Canonical run/artifact statuses used across the app.
 * Keep this list in sync with the backend Zod schema (Chunk 5).
 */
export type StatusKey =
  | "draft"
  | "queued"
  | "running"
  | "awaiting_approval"
  | "awaiting_clarification"
  | "approved"
  | "rejected"
  | "failed"
  | "cancelled"
  | "merged"
  | "open"
  | "review"
  | "paused";

const config: Record<
  StatusKey,
  { label: string; icon: React.ComponentType<{ className?: string }>; className: string }
> = {
  draft: {
    label: "Draft",
    icon: CircleDashed,
    className: "border-border bg-muted text-muted-foreground",
  },
  queued: {
    label: "Queued",
    icon: CircleDot,
    className: "border-border bg-muted text-muted-foreground",
  },
  running: {
    label: "Running",
    icon: Loader2,
    className: "border-info/30 bg-info/10 text-info [&>svg]:animate-spin",
  },
  awaiting_approval: {
    label: "Awaiting approval",
    icon: AlertCircle,
    className: "border-warning/40 bg-warning/15 text-foreground",
  },
  awaiting_clarification: {
    label: "Awaiting clarification",
    icon: AlertCircle,
    className: "border-warning/40 bg-warning/15 text-foreground",
  },
  approved: {
    label: "Approved",
    icon: CheckCircle2,
    className: "border-success/30 bg-success/10 text-success",
  },
  rejected: {
    label: "Rejected",
    icon: XCircle,
    className: "border-destructive/30 bg-destructive/10 text-destructive",
  },
  failed: {
    label: "Failed",
    icon: XCircle,
    className: "border-destructive/30 bg-destructive/10 text-destructive",
  },
  cancelled: {
    label: "Cancelled",
    icon: XCircle,
    className: "border-border bg-muted text-muted-foreground",
  },
  merged: {
    label: "Merged",
    icon: GitMerge,
    className: "border-transparent bg-primary/10 text-primary",
  },
  open: {
    label: "Open",
    icon: GitPullRequest,
    className: "border-info/30 bg-info/10 text-info",
  },
  review: {
    label: "In review",
    icon: AlertCircle,
    className: "border-warning/40 bg-warning/15 text-foreground",
  },
  paused: {
    label: "Paused",
    icon: Pause,
    className: "border-border bg-muted text-muted-foreground",
  },
};

export interface StatusBadgeProps {
  status: StatusKey;
  label?: string;
  className?: string;
  iconOnly?: boolean;
}

export function StatusBadge({ status, label, className, iconOnly }: StatusBadgeProps) {
  const { label: defaultLabel, icon: Icon, className: variantClass } = config[status];
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-xs font-medium [&>svg]:size-3",
        variantClass,
        className,
      )}
      data-status={status}
    >
      <Icon aria-hidden />
      {!iconOnly && <span>{label ?? defaultLabel}</span>}
    </span>
  );
}
