"use client";

import * as React from "react";
import {
  AlertCircle,
  Check,
  CircleDashed,
  Loader2,
  MessagesSquare,
  ShieldAlert,
} from "lucide-react";

import { cn } from "@/lib/utils";
import {
  COPILOT_STAGES,
  type CopilotStage,
  type StageStatusDot,
} from "@/lib/copilot/stages";

export interface PipelineRailStage {
  id: string;
  label: string;
  status: StageStatusDot;
  ownerRole?: string;
  mandatory?: boolean;
}

export interface PipelineRailProps {
  /** Ordered stages with live status; defaults to the full 8-stage catalog. */
  stages?: PipelineRailStage[];
  /** The active stage id (highlighted). */
  active: string;
  /** Click a stage node (e.g. to scroll its transcript). */
  onSelect?: (stageId: string) => void;
  className?: string;
}

const DOT: Record<
  StageStatusDot,
  { icon: React.ComponentType<{ className?: string }>; ring: string; tone: string; label: string }
> = {
  idle: {
    icon: CircleDashed,
    ring: "border-line-soft bg-panel-elevated/50 text-muted-foreground",
    tone: "text-muted-foreground",
    label: "Upcoming",
  },
  interviewing: {
    icon: MessagesSquare,
    ring: "border-brand-bright/50 bg-brand-bright/10 text-brand-bright",
    tone: "text-brand-bright",
    label: "Interviewing",
  },
  running: {
    icon: Loader2,
    ring: "border-info/50 bg-info/10 text-info [&>svg]:animate-spin",
    tone: "text-info",
    label: "Running",
  },
  awaiting_gate: {
    icon: AlertCircle,
    ring: "border-warning/50 bg-warning/15 text-foreground",
    tone: "text-warning",
    label: "Awaiting approval",
  },
  approved: {
    icon: Check,
    ring: "border-success/40 bg-success/10 text-success",
    tone: "text-success",
    label: "Approved",
  },
  rejected: {
    icon: ShieldAlert,
    ring: "border-destructive/40 bg-destructive/10 text-destructive",
    tone: "text-destructive",
    label: "Rejected",
  },
  complete: {
    icon: Check,
    ring: "border-success/40 bg-success/10 text-success",
    tone: "text-success",
    label: "Complete",
  },
};

function toRailStage(s: CopilotStage, status: StageStatusDot): PipelineRailStage {
  return { id: s.id, label: s.label, status, ownerRole: s.ownerRole, mandatory: s.mandatory };
}

export function PipelineRail({ stages, active, onSelect, className }: PipelineRailProps) {
  const rows: PipelineRailStage[] =
    stages ?? COPILOT_STAGES.map((s) => toRailStage(s, "idle"));

  return (
    <nav
      aria-label="Pipeline stages"
      className={cn(
        "border-line-soft bg-panel-elevated/40 flex h-full min-h-0 flex-col overflow-auto border-r",
        className,
      )}
    >
      <div className="border-line-soft border-b px-4 py-4">
        <div className="text-brand-bright flex items-center gap-2 font-mono text-[10.5px] font-semibold uppercase tracking-[0.14em]">
          <span className="from-brand-gradient-from to-brand-gradient-to bg-gradient-to-r bg-clip-text text-transparent">
            ——
          </span>
          Pipeline
        </div>
        <p className="text-muted-foreground mt-1 text-[11px]">
          Requirements &rarr; Documentation
        </p>
      </div>

      <ol className="relative flex-1 space-y-0.5 px-3 py-3">
        {rows.map((stage, i) => {
          const isActive = stage.id === active;
          const cfg = DOT[stage.status];
          const Icon = cfg.icon;
          // Any stage can be re-activated — clicking jumps the Copilot to that
          // agent (`useCopilot().setStage`), even ones ahead of the current one.
          const clickable = !!onSelect;
          const awaiting = stage.status === "awaiting_gate";

          return (
            <li key={stage.id} className="relative">
              {/* connector line */}
              {i < rows.length - 1 && (
                <span
                  aria-hidden
                  className="border-line-soft absolute left-[27px] top-9 h-[calc(100%-1.25rem)] border-l"
                />
              )}
              <button
                type="button"
                disabled={!clickable}
                onClick={clickable ? () => onSelect?.(stage.id) : undefined}
                aria-current={isActive ? "step" : undefined}
                title={
                  clickable
                    ? isActive
                      ? undefined
                      : "Click to work with this agent"
                    : undefined
                }
                className={cn(
                  "group flex w-full items-start gap-3 rounded-[var(--radius)] px-2.5 py-2 text-left transition-colors",
                  isActive && "bg-brand-bright/[0.06] ring-1 ring-brand-bright/25",
                  clickable && !isActive && "hover:bg-panel-elevated/70",
                  !clickable && "cursor-default",
                )}
              >
                <span
                  className={cn(
                    "relative z-10 mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-full border [&>svg]:size-3.5",
                    cfg.ring,
                    isActive && "shadow-[0_0_0_3px_var(--color-brand-bright)]/10",
                  )}
                >
                  <Icon aria-hidden />
                </span>
                <span className="min-w-0 flex-1 pt-0.5">
                  <span className="flex items-center gap-1.5">
                    <span
                      className={cn(
                        "truncate text-[13px] font-semibold",
                        isActive ? "text-foreground" : "text-muted-foreground",
                      )}
                    >
                      {stage.label}
                    </span>
                    {stage.mandatory && (
                      <span
                        className="text-warning/80 font-mono text-[9px] uppercase tracking-wide"
                        title="Mandatory gate — never auto-approved"
                      >
                        req
                      </span>
                    )}
                  </span>
                  <span className={cn("block text-[11px] leading-tight", cfg.tone)}>
                    {cfg.label}
                    {awaiting && stage.ownerRole && (
                      <span className="text-muted-foreground">
                        {" · "}
                        {stage.ownerRole}
                      </span>
                    )}
                  </span>
                </span>
              </button>
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
