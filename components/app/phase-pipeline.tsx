"use client";

import * as React from "react";
import Link from "next/link";
import {
  CheckCircle2,
  CircleDashed,
  CircleDot,
  GitMerge,
  Loader2,
  Lock,
  Pause,
  XCircle,
  type LucideIcon,
} from "lucide-react";

import { cn } from "@/lib/utils";
import { SlaCountdown } from "@/components/app/sla-countdown";
import { PHASE_LABEL as PHASE_LABELS, PHASE_ORDER } from "@/lib/agents";
import { agentsForTrack } from "@/lib/tracks";
import type { DeliveryTrack, Phase, PhasePipelineEntry, Status } from "@/lib/schemas";

const STATUS_ICON: Record<Status, LucideIcon> = {
  draft: CircleDashed,
  queued: CircleDot,
  running: Loader2,
  awaiting_approval: CircleDot,
  awaiting_clarification: CircleDot,
  approved: CheckCircle2,
  rejected: XCircle,
  failed: XCircle,
  cancelled: XCircle,
  merged: GitMerge,
  paused: Pause,
};

const STATUS_TONE: Record<Status, string> = {
  draft: "border-border bg-muted text-muted-foreground",
  queued: "border-border bg-muted text-muted-foreground",
  running: "border-info/40 bg-info/10 text-info [&_svg]:animate-spin",
  awaiting_approval: "border-warning/40 bg-warning/15 text-foreground",
  awaiting_clarification: "border-warning/40 bg-warning/15 text-foreground",
  approved: "border-success/30 bg-success/10 text-success",
  rejected: "border-destructive/30 bg-destructive/10 text-destructive",
  failed: "border-destructive/30 bg-destructive/10 text-destructive",
  cancelled: "border-border bg-muted text-muted-foreground",
  merged: "border-primary/30 bg-primary/10 text-primary",
  paused: "border-border bg-muted text-muted-foreground",
};

/** Node background tone for the vertical pipeline circles */
const NODE_TONE: Record<Status, string> = {
  draft: "border-border bg-muted text-muted-foreground",
  queued: "border-border bg-muted/60 text-muted-foreground",
  running:
    "bg-gradient-to-br from-brand-gradient-from to-brand-gradient-to border-transparent text-white",
  awaiting_approval: "border-warning/40 bg-warning/15 text-foreground",
  awaiting_clarification: "border-warning/40 bg-warning/15 text-foreground",
  approved:
    "bg-gradient-to-br from-success to-[oklch(0.66_0.15_155)] border-transparent text-[oklch(0.14_0.06_155)]",
  rejected: "border-destructive/30 bg-destructive/10 text-destructive",
  failed: "border-destructive/30 bg-destructive/10 text-destructive",
  cancelled: "border-border bg-muted text-muted-foreground",
  merged:
    "bg-gradient-to-br from-success to-[oklch(0.66_0.15_155)] border-transparent text-[oklch(0.14_0.06_155)]",
  paused: "border-border bg-muted text-muted-foreground",
};

const STATUS_LABEL: Record<Status, string> = {
  draft: "Draft",
  queued: "Queued",
  running: "Running",
  awaiting_approval: "Waiting",
  awaiting_clarification: "Waiting",
  approved: "Done",
  rejected: "Rejected",
  failed: "Failed",
  cancelled: "Cancelled",
  merged: "Done",
  paused: "Paused",
};

/** True for terminal-success or in-progress states that get the done stem gradient */
function isDone(status: Status): boolean {
  return status === "approved" || status === "merged";
}

function isActive(status: Status): boolean {
  return status === "running" || status === "awaiting_approval";
}

export interface PhasePipelineProps {
  pipeline: readonly PhasePipelineEntry[];
  /** Which phase is the focus (brighter ring). Defaults to first non-terminal phase. */
  activePhase?: Phase;
  /**
   * If provided, each phase node becomes a link to `hrefFor(phase)`. Return
   * `undefined` for a phase to leave its node non-clickable (e.g. agents that
   * don't have a dedicated page yet) — the node still renders in the pipeline.
   */
  hrefFor?: (phase: Phase) => string | undefined;
  /**
   * Agents this viewer's role cannot reach (`lib/agent-access.ts`).
   *
   * Rendered locked rather than hidden: the pipeline is the project's shape,
   * and dropping stages from it would make the same project look different to
   * different people — you could not ask "what happened at Security?" of
   * someone who cannot see Security. Locked keeps the shape and states the
   * standing.
   */
  lockedPhases?: ReadonlySet<Phase>;
  /** Rendered inside a locked row — the ask, in context. */
  renderLockedAction?: (phase: Phase) => React.ReactNode;
  /** Visual density. "compact" drops the phase labels — used inside project cards. */
  density?: "default" | "compact";
  /**
   * Optional SLA deadline ISO string for the phase currently awaiting approval (D-04).
   * When provided and the active phase status is `awaiting_approval`, an SlaCountdown
   * is rendered inline with the phase row.
   */
  slaDeadline?: string;
  /**
   * The project's delivery track. Determines which agents make up the
   * pipeline (PRD §6) — a Track 4 project has no Design or Code Review stage,
   * and a Track 3 project has Discovery & Assessment and Strategy.
   *
   * Omitted for surfaces with no project in scope, which fall back to the
   * Track 1 roster.
   */
  track?: DeliveryTrack;
  className?: string;
}

export function PhasePipeline({
  pipeline,
  activePhase,
  hrefFor,
  density = "default",
  slaDeadline,
  track,
  className,
  lockedPhases,
  renderLockedAction,
}: PhasePipelineProps) {
  const active =
    activePhase ??
    pipeline.find((p) => p.status === "running" || p.status === "awaiting_approval")?.phase ??
    pipeline[pipeline.length - 1]?.phase;

  // ── Compact (horizontal) variant — used in project cards ──────────────
  if (density === "compact") {
    return (
      <ol
        className={cn("flex w-full items-stretch gap-0", className)}
        aria-label="SDLC phase pipeline"
      >
        {pipeline.map((entry, i) => {
          const Icon = STATUS_ICON[entry.status];
          const isActiveEntry = entry.phase === active;
          const content = (
            <div
              className={cn(
                "relative flex flex-1 items-center gap-2 border px-3 py-2 text-sm transition-colors",
                STATUS_TONE[entry.status],
                isActiveEntry && "ring-ring ring-2 ring-offset-background ring-offset-1",
                i === 0 && "rounded-l-md",
                i === pipeline.length - 1 && "rounded-r-md",
                i > 0 && "-ml-px",
                "px-2 py-1",
              )}
            >
              <Icon className="size-4 shrink-0" aria-hidden />
              <span className="sr-only">{PHASE_LABELS[entry.phase]}: {entry.status.replace("_", " ")}</span>
            </div>
          );
          const href = hrefFor?.(entry.phase);
          return (
            <li key={entry.phase} className="flex flex-1">
              {href ? (
                <Link
                  href={href}
                  className="focus-visible:ring-ring flex flex-1 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-1"
                  aria-current={isActiveEntry ? "step" : undefined}
                >
                  {content}
                </Link>
              ) : (
                content
              )}
            </li>
          );
        })}
      </ol>
    );
  }

  // ── Default (vertical) variant — northstar mission-control timeline ────
  // Build an ordered map so phases always appear in roster order, even if the
  // pipeline data only contains a subset.
  const entryMap = new Map(pipeline.map((e) => [e.phase, e]));

  // The roster is the track's, not a fixed eight (PRD §6). Without a track we
  // fall back to Track 1's ordering.
  const roster = track ? agentsForTrack(track) : PHASE_ORDER;

  return (
    <ol
      className={cn("flex flex-col gap-0", className)}
      aria-label="SDLC phase pipeline"
    >
      {roster.map((phase, i) => {
        const entry = entryMap.get(phase);
        if (!entry) {
          // Phase not present in data — show as queued placeholder
          const phasePlaceholder: PhasePipelineEntry = {
            phase,
            status: "queued",
          };
          return renderPhaseRow(
            phasePlaceholder,
            i,
            roster.length,
            active,
            hrefFor,
            false,
            undefined,
            lockedPhases?.has(phase) ?? false,
            renderLockedAction,
          );
        }
        // Attach SLA deadline only to the phase that is awaiting approval
        const phaseDeadline =
          entry.status === "awaiting_approval" && entry.phase === active
            ? slaDeadline
            : undefined;
        return renderPhaseRow(
          entry,
          i,
          roster.length,
          active,
          hrefFor,
          true,
          phaseDeadline,
          lockedPhases?.has(phase) ?? false,
          renderLockedAction,
        );
      })}
    </ol>
  );
}

function renderPhaseRow(
  entry: PhasePipelineEntry,
  index: number,
  total: number,
  active: Phase | undefined,
  hrefFor: ((phase: Phase) => string | undefined) | undefined,
  hasData: boolean,
  slaDeadline?: string,
  locked = false,
  renderLockedAction?: (phase: Phase) => React.ReactNode,
) {
  const isActivePh = entry.phase === active;
  const done = isDone(entry.status);
  const activeStatus = isActive(entry.status);
  const isLast = index === total - 1;
  const Icon = STATUS_ICON[entry.status];
  const number = index + 1;

  const nodeEl = (
    <div
      className={cn(
        "relative z-10 flex size-[34px] shrink-0 items-center justify-center rounded-full border-[1.5px] text-sm font-semibold transition-all duration-300",
        NODE_TONE[entry.status],
        // Active node: brand glow
        activeStatus &&
          "animate-[glow_2.4s_ease-in-out_infinite] shadow-[0_0_0_4px_oklch(0.6_0.2_35_/_0.18),0_0_22px_oklch(0.6_0.2_35_/_0.5)]",
      )}
      aria-hidden
    >
      {done ? (
        <CheckCircle2 className="size-4" />
      ) : activeStatus ? (
        <Icon className="size-4 animate-spin" />
      ) : (
        <span className="font-mono text-xs">{number}</span>
      )}
    </div>
  );

  const stemEl = !isLast ? (
    <div
      className={cn(
        "mx-auto min-h-[16px] w-0.5 flex-1",
        done
          ? "bg-gradient-to-b from-success to-brand-gradient-from"
          : "bg-line-soft",
      )}
      aria-hidden
    />
  ) : null;

  const statusLabel = (
    <span
      className={cn(
        "shrink-0 self-start rounded-full px-2.5 py-1 font-mono text-[10.5px] font-semibold uppercase tracking-wide",
        done && "bg-success/10 text-success",
        activeStatus && "bg-primary/15 text-brand-bright",
        !done && !activeStatus && "bg-muted text-muted-foreground",
      )}
    >
      {STATUS_LABEL[entry.status]}
    </span>
  );

  const progressBar =
    activeStatus ? (
      <div
        className="mt-2 h-1 w-full overflow-hidden rounded-full bg-muted"
        aria-hidden
      >
        <div className="relative h-full w-[62%] rounded-full bg-gradient-to-r from-brand-gradient-from to-brand-gradient-to">
          {/* shimmer sweep */}
          <span className="absolute inset-0 animate-[shimmer_1.6s_infinite] bg-gradient-to-r from-transparent via-white/40 to-transparent" />
        </div>
      </div>
    ) : null;

  const metaEl = entry.updatedAt ? (
    <p className="mt-0.5 font-mono text-[11px] text-muted-foreground">
      {new Date(entry.updatedAt).toLocaleTimeString(undefined, {
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
      })}
    </p>
  ) : null;

  const rowContent = (
    <div
      className={cn(
        "flex flex-1 items-start justify-between gap-3 py-3 pr-3",
        index > 0 && "border-line-soft border-t",
        // Dimmed as a whole, so the contrast between what you can work on and
        // what you cannot reads down the rail at a glance rather than needing
        // each row inspected for a badge.
        locked && "opacity-55",
      )}
    >
      <div className="min-w-0 flex-1">
        <p
          className={cn(
            "font-display flex items-center gap-1.5 text-[14.5px] font-bold leading-tight tracking-tight",
            (!hasData || locked) && "text-muted-foreground",
          )}
        >
          {locked && <Lock className="size-3.5 shrink-0" aria-hidden />}
          {PHASE_LABELS[entry.phase]}
          {locked && (
            <span className="sr-only">
              {" "}
              — you do not have access to this agent
            </span>
          )}
        </p>
        {metaEl}
        {!locked && progressBar}
        {/* SLA countdown — only for the phase currently awaiting approval (D-04) */}
        {!locked && slaDeadline && entry.status === "awaiting_approval" && (
          <SlaCountdown deadline={slaDeadline} className="mt-2" />
        )}
        {locked && renderLockedAction && (
          <div className="mt-2">{renderLockedAction(entry.phase)}</div>
        )}
      </div>
      {/* The run's status is suppressed on a locked row. "Awaiting approval"
          is a call to act, and showing it to someone who cannot act on this
          agent reads as a task they are ignoring. The lock is the status. */}
      {locked ? (
        <span className="bg-muted text-muted-foreground shrink-0 self-start rounded-full px-2.5 py-1 font-mono text-[10.5px] font-semibold tracking-wide uppercase">
          No access
        </span>
      ) : (
        statusLabel
      )}
    </div>
  );

  return (
    <li key={entry.phase} className="flex gap-3.5 pl-3">
      {/* Rail: node + stem */}
      <div className="flex flex-col items-center">
        {nodeEl}
        {stemEl}
      </div>

      {/* Content */}
      {(() => {
        // A locked row is never a link. Its destination is the agent's own
        // page, which would refuse the viewer — and a link that only ever
        // leads to a wall is worse than no link, because it costs a click to
        // learn what the lock already said.
        const href = locked ? undefined : hrefFor?.(entry.phase);
        return href ? (
          <Link
            href={href}
            className="focus-visible:ring-ring flex flex-1 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-1"
            aria-current={isActivePh ? "step" : undefined}
          >
            {rowContent}
          </Link>
        ) : (
          rowContent
        );
      })()}
    </li>
  );
}
