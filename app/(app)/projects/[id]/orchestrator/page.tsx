"use client";

import * as React from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { Info, PauseCircle, ShieldAlert } from "lucide-react";

import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { LoadingState } from "@/components/ui/loading-state";
import { ErrorState } from "@/components/ui/error-state";
import { RestrictedAccess } from "@/components/auth/restricted-access";
import { StatusBadge } from "@/components/ui/status-badge";
import { useSession } from "@/hooks/use-session";
import { hasPermission } from "@/lib/auth/permissions";
import { effectivePlatformRole } from "@/lib/auth/effective-role";
import { getProject } from "@/lib/api/projects";
import { listApprovals } from "@/lib/api/approvals";
import { qk } from "@/lib/api/query-keys";
import { GATE_POLICY, PHASE_LABEL, phaseHref, ROUTABLE_PHASES } from "@/lib/agents";
import { agentsForTrack, TRACK_META } from "@/lib/tracks";
import { CAPABILITY_CLASS_META } from "@/lib/capability-class";
import { AGENT_OWNER_ROLE, ROLE_META } from "@/lib/roles";
import type { Phase, ProjectId } from "@/lib/schemas";

/**
 * Orchestrator — PRD §34.11 and §15.4.
 *
 * "The Orchestrator is the Project Admin's cross-agent cockpit — a
 * conversation partner, not an automatic sequencer. Nothing runs a fixed
 * script, and nothing auto-advances."
 *
 * So this screen is a *control view*, not a workflow engine: for every agent
 * in the project's track it shows the stage state, who owns its gate, what
 * class its next action is, and whether it is currently holding the work up.
 *
 * Driving the Orchestrator is the Project Admin's alone (PRD §15.5–§15.11 all
 * state "Cannot: drive the Orchestrator"). Contributors see it read-only.
 */
export default function ProjectOrchestratorPage() {
  const params = useParams<{ id: string }>();
  const id = params.id as ProjectId;
  const session = useSession({ required: true });
  const role = effectivePlatformRole(session);

  const projectQ = useQuery({
    queryKey: qk.projects.detail(id),
    queryFn: () => getProject(id),
  });

  const approvalsQ = useQuery({
    queryKey: qk.approvals.list({ project: id }),
    queryFn: () => listApprovals({}),
  });

  if (!hasPermission(session, "artifact:view")) {
    return (
      <RestrictedAccess description="The Orchestrator requires access to this project's artifacts." />
    );
  }

  if (projectQ.isLoading) {
    return (
      <div className="p-4 md:px-10 md:py-8">
        <LoadingState variant="card" />
      </div>
    );
  }

  if (projectQ.isError || !projectQ.data) {
    return (
      <div className="p-4 md:px-10 md:py-8">
        <ErrorState
          title="Couldn't load the project"
          onRetry={() => projectQ.refetch()}
        />
      </div>
    );
  }

  const project = projectQ.data;
  const track = TRACK_META[project.track];
  const roster = agentsForTrack(project.track);
  const entryMap = new Map(project.pipeline.map((e) => [e.phase, e]));

  // Gates currently pending on this project, keyed by phase.
  const pendingByPhase = new Map(
    (approvalsQ.data ?? [])
      .filter((g) => g.projectId === id)
      .map((g) => [g.phase as Phase, g]),
  );

  // The Project Admin drives; everyone else observes (PRD §15.5–§15.11).
  const canDrive = role === "project_admin" || role === "org_admin";

  return (
    <div className="w-full space-y-5 p-4 md:px-10 md:py-8">
      <div>
        <h2 className="font-display text-lg font-semibold tracking-tight">
          Orchestrator
        </h2>
        <p className="text-muted-foreground mt-1 max-w-2xl text-[13px]">
          The cross-agent cockpit for this project&apos;s{" "}
          <span className="text-foreground">
            Track {track.number} · {track.label}
          </span>{" "}
          roster — {roster.length} agents. It is a conversation partner, not an
          automatic sequencer: nothing runs a fixed script and nothing
          auto-advances.
        </p>
      </div>

      {!canDrive && (
        <div className="border-line-soft text-muted-foreground flex items-start gap-2 rounded-lg border border-dashed px-4 py-3 text-[12.5px]">
          <Info className="mt-px size-4 shrink-0" aria-hidden />
          <p>
            You&apos;re viewing the Orchestrator read-only. Driving it across
            agents is the Project Admin&apos;s role — you act on your own
            agent&apos;s gates from{" "}
            <Link href="/approvals" className="text-brand-bright underline underline-offset-2">
              Approvals
            </Link>
            .
          </p>
        </div>
      )}

      {/* ── Stage flow ───────────────────────────────────────────────────── */}
      <ol className="space-y-2">
        {roster.map((phase, i) => {
          const entry = entryMap.get(phase);
          const status = entry?.status ?? "queued";
          const gate = GATE_POLICY[phase];
          const cls = CAPABILITY_CLASS_META[gate.capabilityClass];
          const ownerRole = ROLE_META[AGENT_OWNER_ROLE[phase]];
          const pending = pendingByPhase.get(phase);
          const isBlocking =
            status === "awaiting_approval" || status === "awaiting_clarification";
          const href = ROUTABLE_PHASES.has(phase) ? phaseHref(id, phase) : undefined;

          const inner = (
            <>
              <span className="text-muted-foreground w-6 shrink-0 text-right font-mono text-[11px]">
                {String(i + 1).padStart(2, "0")}
              </span>

              <span className="min-w-0 flex-1">
                <span className="flex flex-wrap items-center gap-2">
                  <span className="text-[13px] font-medium">{PHASE_LABEL[phase]}</span>

                  {/* Capability class of this agent's gate (PRD §13) */}
                  <span
                    className={cn(
                      "rounded-full border px-1.5 py-px font-mono text-[10px] tracking-wide uppercase",
                      cls.chipClass,
                    )}
                    title={cls.meaning}
                  >
                    {cls.label}
                  </span>

                  {gate.mandatory && (
                    <span
                      className="text-destructive inline-flex items-center gap-1 font-mono text-[10px] tracking-wide uppercase"
                      title="A mandatory checkpoint — it cannot be waived, by the owner or the fallback."
                    >
                      <ShieldAlert className="size-3" aria-hidden />
                      Mandatory
                    </span>
                  )}
                </span>

                <span className="text-muted-foreground mt-0.5 block text-[11.5px]">
                  Gate owner: {ownerRole.label}
                  {!href && " · agent page not yet available"}
                </span>
              </span>

              {isBlocking && (
                <span className="text-warning inline-flex shrink-0 items-center gap-1 font-mono text-[10.5px] tracking-wide uppercase">
                  <PauseCircle className="size-3.5" aria-hidden />
                  Holding
                </span>
              )}

              {pending?.deadline && (
                <span className="text-muted-foreground hidden shrink-0 font-mono text-[11px] sm:block">
                  SLA set
                </span>
              )}

              <StatusBadge status={status} className="shrink-0" />
            </>
          );

          return (
            <li key={phase}>
              {href ? (
                <Link
                  href={href}
                  className={cn(
                    "border-line-soft bg-panel-elevated hover:border-primary/40 flex items-center gap-3 rounded-lg border px-3 py-2.5 transition-colors",
                    isBlocking && "border-warning/40",
                  )}
                >
                  {inner}
                </Link>
              ) : (
                <div
                  className={cn(
                    "border-line-soft bg-surface-1 flex items-center gap-3 rounded-lg border border-dashed px-3 py-2.5",
                    isBlocking && "border-warning/40",
                  )}
                >
                  {inner}
                </div>
              )}
            </li>
          );
        })}
      </ol>

      {/* Legend — the three classes, stated once (PRD §13). */}
      <div className="border-line-soft flex flex-wrap items-center gap-x-5 gap-y-2 rounded-lg border px-4 py-3">
        <span className="text-muted-foreground font-mono text-[10px] tracking-widest uppercase">
          Capability classes
        </span>
        {(["safe", "consequential", "signoff"] as const).map((c) => {
          const m = CAPABILITY_CLASS_META[c];
          return (
            <span key={c} className="flex items-center gap-1.5">
              <span className={cn("size-2 rounded-full", m.dotClass)} aria-hidden />
              <span className="text-[12px] font-medium">{m.label}</span>
              <span className="text-muted-foreground text-[11.5px]">{m.uiBehaviour}</span>
            </span>
          );
        })}
      </div>

      {/* Track 2 enters at whichever stage the change requires (PRD §8). */}
      {project.track === "enhancement" && (
        <div className="border-line-soft text-muted-foreground rounded-lg border border-dashed px-4 py-3 text-[12.5px]">
          <Badge variant="secondary" className="mr-2 font-mono text-[10px]">
            Track 2
          </Badge>
          This track is entered at whichever stage the change actually requires
          — not always at Requirements. The Design agent is skipped entirely
          when no design update is needed.
        </div>
      )}
    </div>
  );
}
