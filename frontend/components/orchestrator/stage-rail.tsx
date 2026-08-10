"use client";

import * as React from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import {
  CheckCircle2,
  Circle,
  FileText,
  Loader2,
  PauseCircle,
  ShieldAlert,
  Timer,
  XCircle,
} from "lucide-react";

import { cn } from "@/lib/utils";
import { StatusBadge } from "@/components/ui/status-badge";
import { GATE_POLICY, PHASE_LABEL, ROUTABLE_PHASES, phaseHref } from "@/lib/agents";
import { CAPABILITY_CLASS_META } from "@/lib/capability-class";
import { listApprovals } from "@/lib/api/approvals";
import { getProject } from "@/lib/api/projects";
import { qk } from "@/lib/api/query-keys";
import type { StageRun, StageRunStatus } from "@/lib/orchestrator/types";
import type { Phase, ProjectId } from "@/lib/schemas";

const STATUS_ICON: Record<StageRunStatus, React.ComponentType<{ className?: string }>> = {
  pending: Circle,
  running: Loader2,
  awaiting_gate: PauseCircle,
  approved: CheckCircle2,
  rejected: XCircle,
  skipped: Circle,
};

const STATUS_TONE: Record<StageRunStatus, string> = {
  pending: "text-muted-foreground/50",
  running: "text-info",
  awaiting_gate: "text-warning",
  approved: "text-success",
  rejected: "text-destructive",
  skipped: "text-muted-foreground/40",
};

export interface StageRailProps {
  stages: StageRun[];
  cursor: number;
  projectId: string | null;
  className?: string;
}

/**
 * Live state of the orchestrated run, stage by stage.
 *
 * This is the reason the page is an Orchestrator rather than a chat window: the
 * thread says what was decided, the rail says where the run *is*. Mandatory
 * gates are marked here as well as in the thread, because "why did it stop"
 * must be answerable without scrolling back.
 */
export function StageRail({ stages, cursor, projectId, className }: StageRailProps) {
  // ── The project's *actual* state, alongside the run's ─────────────────────
  //
  // The run above is one orchestrated pass; this is what is really happening on
  // the project right now — the signal the read-only control view used to carry
  // before this rail replaced it. Kept as a separate, quieter line rather than
  // merged into the run status, because "my run approved Design" and "Design is
  // holding on a real approver" are different facts and collapsing them would
  // make the rail lie in exactly the situation it matters most.
  const projectQ = useQuery({
    queryKey: qk.projects.detail((projectId ?? "") as ProjectId),
    queryFn: () => getProject(projectId as ProjectId),
    enabled: !!projectId,
  });

  const approvalsQ = useQuery({
    queryKey: qk.approvals.list({ project: projectId }),
    queryFn: () => listApprovals({}),
    enabled: !!projectId,
  });

  const liveByPhase = React.useMemo(
    () => new Map((projectQ.data?.pipeline ?? []).map((e) => [e.phase, e.status])),
    [projectQ.data],
  );

  const pendingByPhase = React.useMemo(
    () =>
      new Map(
        (approvalsQ.data ?? [])
          .filter((g) => String(g.projectId) === projectId)
          .map((g) => [g.phase as Phase, g]),
      ),
    [approvalsQ.data, projectId],
  );

  return (
    <aside
      className={cn(
        "border-line-soft bg-surface-1 flex h-full w-[276px] shrink-0 flex-col border-l",
        className,
      )}
    >
      <div className="border-line-soft border-b px-3 py-2.5">
        <span className="text-muted-foreground font-mono text-[10px] font-semibold tracking-[0.14em] uppercase">
          Pipeline
        </span>
      </div>

      <ol className="min-h-0 flex-1 space-y-1 overflow-y-auto p-2">
        {stages.map((stage, i) => {
          const Icon = STATUS_ICON[stage.status];
          const gate = GATE_POLICY[stage.phase];
          const cls = CAPABILITY_CLASS_META[gate.capabilityClass];
          const isCurrent = i === cursor && stage.status !== "pending";

          const liveStatus = liveByPhase.get(stage.phase);
          const pending = pendingByPhase.get(stage.phase);
          const isHolding =
            liveStatus === "awaiting_approval" || liveStatus === "awaiting_clarification";
          // Only worth a line when the project has actually moved — "queued"
          // on every stage is the default state and says nothing.
          const showLive = !!liveStatus && (liveStatus !== "queued" || isHolding);
          const href =
            projectId && ROUTABLE_PHASES.has(stage.phase)
              ? phaseHref(projectId, stage.phase)
              : undefined;

          const heading = (
            <span className="flex min-w-0 items-center gap-2">
              <Icon
                className={cn(
                  "size-3.5 shrink-0",
                  STATUS_TONE[stage.status],
                  stage.status === "running" && "animate-spin",
                )}
                aria-hidden
              />
              <span className="truncate text-[12.5px] font-medium">
                {PHASE_LABEL[stage.phase]}
              </span>
              {gate.mandatory && (
                <ShieldAlert
                  className="text-destructive size-3 shrink-0"
                  aria-hidden
                  // Titles on the icon, not a chip — the rail is narrow and the
                  // roster can be ten stages long.
                />
              )}
            </span>
          );

          return (
            <li
              key={stage.phase}
              className={cn(
                "rounded-lg border px-2.5 py-2 transition-colors",
                isCurrent
                  ? "border-primary/40 bg-panel-elevated"
                  : "border-transparent",
                stage.status === "awaiting_gate" && "border-warning/45 bg-warning/[0.05]",
                stage.status === "rejected" && "border-destructive/45 bg-destructive/[0.05]",
              )}
            >
              <div className="flex items-start gap-2">
                <span className="text-muted-foreground mt-px w-5 shrink-0 text-right font-mono text-[10px]">
                  {String(i + 1).padStart(2, "0")}
                </span>
                <div className="min-w-0 flex-1">
                  {href ? (
                    <Link href={href} className="hover:text-brand-bright block transition-colors">
                      {heading}
                    </Link>
                  ) : (
                    heading
                  )}

                  <span
                    className={cn(
                      "mt-1 inline-block rounded-full border px-1.5 py-px font-mono text-[9px] tracking-wide uppercase",
                      cls.chipClass,
                    )}
                    title={cls.meaning}
                  >
                    {cls.label}
                  </span>

                  {/* Who owns this gate (PRD §14.7 ownership matrix). Carried
                      over from the control view this rail replaced — "who has
                      to decide" is the first question a stopped run raises. */}
                  <span className="text-muted-foreground mt-1 block truncate text-[11px]">
                    Gate owner: {gate.ownerLabel}
                  </span>

                  {/* What the project itself is doing, independent of this run. */}
                  {showLive && (
                    <span className="mt-1.5 flex flex-wrap items-center gap-1.5">
                      <span className="text-muted-foreground/70 font-mono text-[9px] tracking-wide uppercase">
                        Live
                      </span>
                      <StatusBadge
                        status={liveStatus}
                        className="px-1.5 py-0 text-[10px] [&>svg]:size-2.5"
                      />
                      {isHolding && (
                        <span
                          className="text-warning inline-flex items-center gap-1 font-mono text-[9.5px] tracking-wide uppercase"
                          title="A real approver is holding this stage — act on it from Approvals."
                        >
                          <PauseCircle className="size-2.5" aria-hidden />
                          Holding
                        </span>
                      )}
                      {pending?.deadline && (
                        <span
                          className="text-muted-foreground inline-flex items-center gap-1 font-mono text-[9.5px] tracking-wide uppercase"
                          title={`SLA deadline: ${pending.deadline}`}
                        >
                          <Timer className="size-2.5" aria-hidden />
                          SLA
                        </span>
                      )}
                    </span>
                  )}

                  {stage.artifacts.length > 0 && (
                    <ul className="mt-1.5 space-y-0.5">
                      {stage.artifacts.map((a) => (
                        <li
                          key={a}
                          className="text-muted-foreground flex items-center gap-1.5 text-[11px]"
                        >
                          <FileText className="size-2.5 shrink-0" aria-hidden />
                          <span className="truncate font-mono">{a}</span>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              </div>
            </li>
          );
        })}
      </ol>

      <div className="border-line-soft text-muted-foreground space-y-1 border-t px-3 py-2.5 text-[11px] leading-relaxed">
        <span className="flex items-center gap-1.5">
          <ShieldAlert className="text-destructive size-3 shrink-0" aria-hidden />
          Mandatory gate — never auto-approved.
        </span>
        <span className="block">
          <span className="font-mono text-[9px] tracking-wide uppercase">Live</span> is the
          project&apos;s real stage state, not this run&apos;s.
        </span>
      </div>
    </aside>
  );
}
