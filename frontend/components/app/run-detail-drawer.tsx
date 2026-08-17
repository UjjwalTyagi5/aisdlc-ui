"use client";

import * as React from "react";
import Link from "next/link";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Bot, ExternalLink, RotateCcw, User } from "lucide-react";
import { formatDistanceToNow } from "date-fns";

import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { StatusBadge } from "@/components/ui/status-badge";
import { LoadingState } from "@/components/ui/loading-state";
import { ActivityTimeline } from "@/components/app/activity-timeline";
import { ApprovalCard } from "@/components/app/approval-card";
import { ClarificationCard } from "@/components/app/clarification-card";
import { PhasePipeline } from "@/components/app/phase-pipeline";
import { AGENT_LABEL } from "@/lib/agents";
import { RunStreamFeed } from "@/components/app/stream-subscriber";
import { AuditTab } from "@/components/app/audit/audit-tab";
import { EvalIndicator } from "@/components/app/eval/eval-indicator";
import { advanceCopilotRun, getRun, getRunSteps } from "@/lib/api/runs";
import { getTrace } from "@/lib/api/traces";
import { TraceSpanTree } from "@/components/app/trace-span-tree";
import { qk } from "@/lib/api/query-keys";
import { approvePermissionForPhase, hasPermission } from "@/lib/auth/permissions";
import { useSession } from "@/hooks/use-session";
import type { ApprovalDecision, Run } from "@/lib/schemas";

export interface RunDetailDrawerProps {
  run: Run | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onReplay?: (run: Run) => void | Promise<void>;
  replayBusy?: boolean;
}

export function RunDetailDrawer({
  run,
  open,
  onOpenChange,
  onReplay,
  replayBusy,
}: RunDetailDrawerProps) {
  // RBAC gate (REQ-M7-12 frontend half / milestone-7.2 plan 05): the
  // phase-approval action is hidden when the session lacks the matching
  // artifact:approve_<phase> permission. Defense-in-depth/UX only — the
  // backend require_permission + _check_permission_for_phase remain the
  // authoritative boundary (T-7.2-25, accepted: a tampered client still 403s).
  const session = useSession();

  // Refresh the run from the BFF so the drawer always shows the latest status/cost.
  // Falls back to the table row's `run` when the query hasn't resolved yet.
  const queryClient = useQueryClient();
  const runQ = useQuery({
    queryKey: run ? qk.runs.detail(run.id) : ["no-run"],
    queryFn: () => getRun(run!.id),
    enabled: !!run,
    initialData: run ?? undefined,
  });
  const liveRun = runQ.data ?? run;

  const stepsQ = useQuery({
    queryKey: run ? qk.runs.steps(run.id) : ["no-run"],
    queryFn: () => getRunSteps(run!.id),
    enabled: !!run,
  });

  // Langfuse-sourced trace for this run (dummy route resolves by run id today).
  const traceQ = useQuery({
    queryKey: run ? qk.traces.detail(run.id) : ["no-run-trace"],
    queryFn: () => getTrace(run!.id),
    enabled: !!run,
    retry: false,
  });

  // Tab state — "overview" is the default (existing content); "audit" is gated on artifact:view.
  const [activeTab, setActiveTab] = React.useState<"overview" | "audit">("overview");

  // Approval gate state — pending/pendingDecision track the in-flight signal.
  const [approvalPending, setApprovalPending] = React.useState(false);
  const [pendingDecision, setPendingDecision] =
    React.useState<ApprovalDecision | null>(null);

  // Clarification gate state (REQ-M10-06) — tracks the in-flight
  // within_agent_clarification signal.
  const [clarificationPending, setClarificationPending] = React.useState(false);

  const handleApprove = React.useCallback(async () => {
    if (!liveRun) return;
    setApprovalPending(true);
    setPendingDecision("approve");
    try {
      // The stage comes from the run itself rather than the caller: the gate that
      // is open is the one on the current stage, and letting the drawer name a
      // different one would let it approve a stage nobody is looking at.
      await advanceCopilotRun(liveRun.id, {
        decision: "approved",
        stage: liveRun.phase,
      });
      // Invalidate the run detail so the drawer refreshes with the new status.
      await queryClient.invalidateQueries({ queryKey: qk.runs.detail(liveRun.id) });
    } finally {
      setApprovalPending(false);
      setPendingDecision(null);
    }
  }, [liveRun, queryClient]);

  const handleReject = React.useCallback(
    async (reason: string) => {
      if (!liveRun) return;
      setApprovalPending(true);
      setPendingDecision("reject");
      try {
        await advanceCopilotRun(liveRun.id, {
          decision: "rejected",
          stage: liveRun.phase,
          reason,
        });
        await queryClient.invalidateQueries({ queryKey: qk.runs.detail(liveRun.id) });
      } finally {
        setApprovalPending(false);
        setPendingDecision(null);
      }
    },
    [liveRun, queryClient],
  );

  // Answer a clarification the agent raised. It resolves the gate through the same
  // advance endpoint as an approval, with the answer carried as the reason — one
  // way for a run to move, whatever the gate was asking. The durable-signal version
  // of this needed a workflow engine to receive it and went with Temporal.
  const handleAnswer = React.useCallback(
    async (answer: string) => {
      if (!liveRun) return;
      setClarificationPending(true);
      try {
        await advanceCopilotRun(liveRun.id, {
          decision: "approved",
          stage: liveRun.phase,
          reason: answer,
        });
        await queryClient.invalidateQueries({ queryKey: qk.runs.detail(liveRun.id) });
      } finally {
        setClarificationPending(false);
      }
    },
    [liveRun, queryClient],
  );

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        side="right"
        className="flex w-[min(680px,96vw)] flex-col gap-0 overflow-hidden p-0"
      >
        {run ? (
          // Use live data from BFF refresh; fall back to the row-click snapshot
          // until the query resolves (avoids flicker on open).
          (() => {
            const r = liveRun ?? run;
            // Hide the gated approve/reject actions when the session lacks
            // the artifact:approve_<phase> permission for this run's CURRENT
            // phase (and isn't admin:*). hasPermission/approvePermissionForPhase
            // mirror the backend has_permission + _PHASE_PERMISSION exactly.
            const approvalPermission = r.phase
              ? approvePermissionForPhase(r.phase)
              : null;
            const canApprove = approvalPermission
              ? hasPermission(session, approvalPermission)
              : false;
            // Clarification answer gate (REQ-M10-06) — same permission as
            // the phase approval gate (the person who approves the phase
            // can answer its agent's questions, per RESEARCH).
            const canAnswer = canApprove;
            // RBAC gate for Audit tab — hidden (not disabled) when lacking artifact:view.
            // Defense-in-depth: backend require_permission is the authoritative gate (T-M8-19).
            const canViewAudit = hasPermission(session, "artifact:view");
            // Eval quality indicator shares the artifact:view gate (REQ-M9-14, T-9.3-11).
            const canViewEval = canViewAudit;
            return (
          <>
            {/* ── Editorial header ───────────────────────────────────── */}
            <SheetHeader className="border-line-soft bg-panel-elevated/60 space-y-2 border-b px-6 py-5 text-left backdrop-blur-sm">
              {/* Eyebrow: mono brand-bright label + project link */}
              <div className="text-brand-bright flex items-center gap-2 font-mono text-[11px] font-semibold uppercase tracking-[0.14em]">
                <span className="from-brand-gradient-from to-brand-gradient-to bg-gradient-to-r bg-clip-text text-transparent">
                  ——
                </span>
                <span>
                  Active run
                  {" · "}
                  <Link
                    href={`/projects/${r.projectId}`}
                    className="hover:text-foreground underline-offset-2 hover:underline"
                  >
                    {r.projectId}
                  </Link>
                </span>
              </div>

              {/* Run title + id */}
              <div className="flex items-start justify-between gap-4">
                <div className="min-w-0 flex-1">
                  <SheetTitle className="font-display truncate text-[22px] font-bold leading-tight tracking-tight">
                    {r.title}
                  </SheetTitle>
                  <SheetDescription className="mt-0.5 flex flex-wrap items-center gap-1.5 font-mono text-[11px] text-muted-foreground">
                    <span>{r.id}</span>
                    <span className="opacity-40">·</span>
                    <span>{AGENT_LABEL[r.agent]}</span>
                  </SheetDescription>
                </div>
                <StatusBadge status={r.status} />
              </div>

              {/* Metric strip — elapsed + trigger */}
              <div className="text-muted-foreground grid grid-cols-3 gap-3 pt-1 text-xs">
                <MetricTile
                  label="Started"
                  value={formatDistanceToNow(new Date(r.startedAt), { addSuffix: true })}
                />
                <MetricTile
                  label="Duration"
                  value={
                    r.durationMs !== null
                      ? formatMs(r.durationMs)
                      : r.status === "running" ||
                          r.status === "awaiting_approval" ||
                          r.status === "awaiting_clarification"
                        ? "in flight"
                        : "—"
                  }
                  mono
                />
                <MetricTile
                  label="Trigger"
                  value={r.trigger}
                  icon={r.trigger === "webhook" ? <Bot className="size-3" aria-hidden /> : <User className="size-3" aria-hidden />}
                />
              </div>

              {/* Cost meter (compact row, not the full tile) */}
              <Separator className="border-line-soft opacity-60" />
              <CostRow cost={r.cost} />

              {/* Eval quality indicator — gated on artifact:view (T-9.3-11, REQ-M9-14) */}
              {canViewEval && (
                <div className="flex items-center gap-2 pt-0.5">
                  <span className="text-muted-foreground text-[10px] uppercase tracking-wider">
                    Quality
                  </span>
                  <EvalIndicator runId={r.id} />
                </div>
              )}

              {/* Replay action */}
              {onReplay && (
                <div className="flex justify-end pt-1">
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => onReplay(r)}
                    disabled={replayBusy}
                    aria-busy={replayBusy}
                    className="border-line-soft h-8 text-xs"
                  >
                    <RotateCcw className="size-3.5" aria-hidden />
                    Replay run
                  </Button>
                </div>
              )}
            </SheetHeader>

            {/* ── Tabs: Overview | Audit ──────────────────────────────── */}
            {/* UI-SPEC Contract 1: TabsList immediately below SheetHeader */}
            <Tabs
              value={activeTab}
              onValueChange={(v) => setActiveTab(v as "overview" | "audit")}
              className="flex flex-1 flex-col overflow-hidden"
            >
              <TabsList
                className="w-full justify-start rounded-none border-b bg-transparent px-6"
              >
                <TabsTrigger
                  value="overview"
                  className="rounded-none pb-3 pt-2 data-[state=active]:border-b-2 data-[state=active]:border-primary data-[state=active]:shadow-none"
                >
                  Overview
                </TabsTrigger>
                {/* Audit tab trigger — hidden entirely when lacking artifact:view (T-M8-19) */}
                {canViewAudit && (
                  <TabsTrigger
                    value="audit"
                    className="rounded-none pb-3 pt-2 data-[state=active]:border-b-2 data-[state=active]:border-primary data-[state=active]:shadow-none"
                  >
                    Audit
                  </TabsTrigger>
                )}
              </TabsList>

              {/* ── Overview tab ── */}
              <TabsContent value="overview" className="flex-1 overflow-auto mt-0">
                {/* Approval card — render when awaiting_approval (ED-5 / D-04) */}
                {r.status === "awaiting_approval" && (
                  <div className="border-line-soft border-b px-6 py-4">
                    <ApprovalCard
                      status={r.status}
                      title="Gate: Approval required"
                      description={
                        <span>
                          This run is paused at an approval gate
                          {r.pendingApprovers && r.pendingApprovers.length > 0 && (
                            <>
                              {" — awaiting "}
                              <span className="font-mono text-xs">
                                {r.pendingApprovers.join(", ")}
                              </span>
                            </>
                          )}
                          .
                        </span>
                      }
                      onApprove={canApprove ? handleApprove : undefined}
                      onReject={canApprove ? handleReject : undefined}
                      pending={approvalPending}
                      pendingDecision={pendingDecision}
                      className="border-warning/30 bg-warning/5"
                    />
                  </div>
                )}

                {/* Clarification card — render when awaiting_clarification (REQ-M10-06) */}
                {r.status === "awaiting_clarification" && (
                  <div className="border-line-soft border-b px-6 py-4">
                    <ClarificationCard
                      questions={r.clarificationQuestions ?? []}
                      clarificationId={r.id}
                      deadline={r.clarificationDeadline ?? undefined}
                      onSubmit={handleAnswer}
                      pending={clarificationPending}
                      canAnswer={canAnswer}
                      className="border-warning/30 bg-warning/5"
                    />
                  </div>
                )}

                {/* Phase pipeline section */}
                <div className="border-line-soft border-b px-6 py-4">
                  <h3 className="text-muted-foreground mb-3 text-[11px] font-semibold uppercase tracking-wider">
                    Phase Pipeline
                  </h3>
                  <PhasePipeline
                    pipeline={r.phase
                      ? [{ phase: r.phase, status: r.status }]
                      : []}
                    activePhase={r.phase}
                    density="default"
                    slaDeadline={
                      r.status === "awaiting_clarification" && r.clarificationDeadline
                        ? // Clarification deadlines come from the workflow's
                          // signal-wait SLA (REQ-M10-06) — prefer the real
                          // value when present.
                          r.clarificationDeadline
                        : r.status === "awaiting_approval" ||
                            r.status === "awaiting_clarification"
                          ? // Derive the SLA deadline: 24 h from when the run started.
                            // The server exposes a configurable SLA; until the field lands
                            // on the Run schema this client-side fallback uses the D-04
                            // default of 24 h so the countdown is always visible.
                            new Date(
                              new Date(r.startedAt).getTime() + 24 * 60 * 60 * 1_000,
                            ).toISOString()
                          : undefined
                    }
                  />
                </div>

                {/* Live stream feed — per-run SSE events (northstar stream panel) */}
                {(r.status === "running" ||
                  r.status === "awaiting_approval" ||
                  r.status === "awaiting_clarification") && (
                  <div className="border-line-soft border-b">
                    <RunStreamFeed runId={r.id} />
                  </div>
                )}

                {/* Steps section */}
                <div className="px-6 py-4">
                  <h3 className="text-muted-foreground mb-3 text-[11px] font-semibold uppercase tracking-wider">
                    Steps
                  </h3>
                  {stepsQ.isLoading ? (
                    <LoadingState variant="list" rows={4} />
                  ) : stepsQ.data && stepsQ.data.length > 0 ? (
                    <ActivityTimeline steps={stepsQ.data} />
                  ) : (
                    <p className="text-muted-foreground text-sm">No steps recorded.</p>
                  )}
                </div>

                {/* Trace section — Langfuse span waterfall for this run */}
                <div className="border-line-soft border-t px-6 py-4">
                  <h3 className="text-muted-foreground mb-3 text-[11px] font-semibold uppercase tracking-wider">
                    Trace
                  </h3>
                  {traceQ.isLoading ? (
                    <LoadingState variant="list" rows={3} />
                  ) : traceQ.data ? (
                    <TraceSpanTree spans={traceQ.data.spans} totalMs={traceQ.data.latencyMs} />
                  ) : (
                    <p className="text-muted-foreground text-sm">No trace recorded.</p>
                  )}
                </div>

                {/* Full-view link */}
                <div className="border-line-soft text-muted-foreground border-t px-6 py-3 text-xs">
                  <Link
                    href={`/runs/${r.id}`}
                    className="hover:text-foreground inline-flex items-center gap-1 underline-offset-2 hover:underline"
                  >
                    <ExternalLink className="size-3" aria-hidden />
                    Open full run view
                  </Link>
                </div>
              </TabsContent>

              {/* ── Audit tab — only rendered when canViewAudit is true ── */}
              {canViewAudit && (
                <TabsContent value="audit" className="flex flex-1 flex-col overflow-hidden mt-0">
                  <AuditTab runId={r.id} />
                </TabsContent>
              )}
            </Tabs>
          </>
            );
          })()
        ) : (
          <SheetHeader className="p-6">
            <SheetTitle>No run selected</SheetTitle>
          </SheetHeader>
        )}
      </SheetContent>
    </Sheet>
  );
}

// ── Sub-components ────────────────────────────────────────────────────────────

function MetricTile({
  label,
  value,
  mono,
  icon,
}: {
  label: string;
  value: React.ReactNode;
  mono?: boolean;
  icon?: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-muted-foreground text-[10px] uppercase tracking-wider">{label}</span>
      <span className={cn("text-foreground flex items-center gap-1 text-xs", mono && "font-mono")}>
        {icon}
        {value}
      </span>
    </div>
  );
}

function CostRow({ cost }: { cost: Run["cost"] }) {
  function formatUsd(n: number): string {
    if (n >= 1) return `$${n.toFixed(2)}`;
    if (n < 0.01) return `$${n.toFixed(4)}`;
    return `$${n.toFixed(3)}`;
  }

  function formatTokens(n: number): string {
    if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
    if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
    return String(n);
  }

  const total = cost.inputTokens + cost.outputTokens;

  return (
    <div className="flex items-center justify-between gap-3">
      <div className="flex items-baseline gap-1.5">
        <span className="font-display text-xl font-bold tracking-tight text-foreground">
          {formatUsd(cost.usd)}
        </span>
        <span className="text-muted-foreground font-mono text-[11px]">
          · {formatTokens(total)} tok
        </span>
      </div>
      <div className="text-muted-foreground font-mono text-[10.5px]">
        <span>in: {formatTokens(cost.inputTokens)}</span>
        <span className="mx-1 opacity-40">·</span>
        <span>out: {formatTokens(cost.outputTokens)}</span>
      </div>
    </div>
  );
}

function formatMs(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60_000) return `${Math.round(ms / 1000)}s`;
  const m = Math.floor(ms / 60_000);
  const s = Math.round((ms % 60_000) / 1000);
  return `${m}m ${s}s`;
}
