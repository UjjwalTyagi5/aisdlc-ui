"use client";

import * as React from "react";
import { Check, CheckCircle2, ChevronDown, CircleDashed, PauseCircle, PlayCircle } from "lucide-react";
import type { LucideIcon } from "lucide-react";

import { cn } from "@/lib/utils";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  PROJECT_DELIVERY_STATUS_LABEL,
  PROJECT_DELIVERY_STATUS_ORDER,
  type ProjectDeliveryStatus,
} from "@/lib/schemas/project";

/**
 * Where a project stands as delivery work — the human-set status, distinct from
 * the approval state and from what the agents are doing right now (see
 * `ProjectDeliveryStatus` in lib/schemas/project.ts).
 *
 * Unlike `ApprovalStatusBadge`, this one renders in every state including the
 * default: "Not started" is information a reader wants, not an exception, and a
 * project list where some cards carry a status chip and others carry none reads
 * as missing data rather than as a meaningful default.
 */
const TONE: Record<ProjectDeliveryStatus, { className: string; icon: LucideIcon }> = {
  not_started: {
    className: "border-line-soft bg-surface-2 text-muted-foreground",
    icon: CircleDashed,
  },
  in_progress: {
    className: "border-info/30 bg-info/10 text-info",
    icon: PlayCircle,
  },
  on_hold: {
    className: "border-warning/30 bg-warning/10 text-warning",
    icon: PauseCircle,
  },
  completed: {
    className: "border-success/30 bg-success/10 text-success",
    icon: CheckCircle2,
  },
};

export function DeliveryStatusBadge({
  status,
  className,
  trailing,
}: {
  status: ProjectDeliveryStatus;
  className?: string;
  /** Rendered inside the pill, after the label — the picker's chevron. */
  trailing?: React.ReactNode;
}) {
  const { className: tone, icon: Icon } = TONE[status];
  return (
    <span
      className={cn(
        // whitespace-nowrap because these labels are two words: without it the
        // pill wraps to "ON / HOLD" the moment its container is tight.
        "inline-flex shrink-0 items-center gap-1 rounded-full border px-2 py-0.5 font-mono text-[10px] font-semibold tracking-wider whitespace-nowrap uppercase",
        tone,
        className,
      )}
    >
      <Icon className="size-2.5 shrink-0" aria-hidden />
      {PROJECT_DELIVERY_STATUS_LABEL[status]}
      {trailing}
    </span>
  );
}

/**
 * The same badge, made editable for the three roles that own a project's
 * delivery state (Project, Business Unit and Organization Admin).
 *
 * Renders the read-only badge for everyone else rather than a disabled control:
 * a greyed-out dropdown invites a click that will never work, and the status
 * itself is not the part a contributor is missing.
 */
export function DeliveryStatusPicker({
  status,
  canEdit,
  onChange,
  busy,
  className,
}: {
  status: ProjectDeliveryStatus;
  canEdit: boolean;
  onChange: (next: ProjectDeliveryStatus) => void;
  busy?: boolean;
  className?: string;
}) {
  if (!canEdit) return <DeliveryStatusBadge status={status} className={className} />;

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          disabled={busy}
          aria-label="Change delivery status"
          className={cn(
            "group rounded-full focus-visible:ring-ring focus-visible:ring-2 focus-visible:outline-none disabled:opacity-50",
            className,
          )}
        >
          <DeliveryStatusBadge
            status={status}
            className="group-hover:brightness-125"
            trailing={<ChevronDown className="size-3 shrink-0 opacity-70" aria-hidden />}
          />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="border-line-soft bg-panel-elevated w-52">
        <DropdownMenuLabel className="text-muted-foreground font-mono text-[9.5px] tracking-widest uppercase">
          Delivery status
        </DropdownMenuLabel>
        <DropdownMenuSeparator className="bg-line-soft" />
        {PROJECT_DELIVERY_STATUS_ORDER.map((s) => (
          <DropdownMenuItem
            key={s}
            onSelect={() => {
              if (s !== status) onChange(s);
            }}
            className="gap-2"
          >
            <span className="flex-1 text-[13px]">{PROJECT_DELIVERY_STATUS_LABEL[s]}</span>
            {s === status && <Check className="text-brand-bright size-3.5" aria-hidden />}
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
