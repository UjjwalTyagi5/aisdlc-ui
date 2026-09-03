"use client";

/**
 * The approval queue for anything that reaches an environment.
 *
 * WHAT THIS SCREEN HAS TO GET RIGHT is the same thing the backend does: never let a
 * deployment look further along than it is. Four states, and each reads differently
 * because each needs a different thing from the person looking:
 *
 *   pending    somebody has to decide. The only state with a decision to make.
 *   approved   decided, but NOT YET RUN. This is the one a UI would naturally show as
 *              a green tick and be wrong about — the approval released it, it has not
 *              happened. It carries the button that makes it happen.
 *   running    started, outcome unknown. Not a success.
 *   settled    succeeded, or failed WITH THE STAGE THAT FAILED.
 *
 * `RequireRole capability=` uses the coarse UI-level Capability type, which decides
 * what is SHOWN. It is NOT the backend permission catalogue: the real gate is
 * artifact:approve_deployment, enforced by the route. Someone who passes the UI check
 * but not that one gets the backend's own refusal in the error banner, which is a
 * better outcome than a button that silently does nothing.
 *
 * Approval and execution are two clicks on purpose. Approving is a decision recorded in
 * the database; executing is a call to Azure DevOps that can be slow and can fail, and
 * folding it into the approval would mean a decision that could not be saved because a
 * deploy timed out.
 */

import * as React from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle, CheckCircle2, Clock, Loader2, PlayCircle, RefreshCw, RotateCw,
  ShieldAlert, XCircle,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { LoadingState } from "@/components/ui/loading-state";
import { RequireRole } from "@/components/auth/require-role";
import {
  approveDeployment, executeDeployment, listDeployments, refreshDeployment,
  rejectDeployment,
} from "@/lib/api/deployment";
import type { DeploymentRequest, FailedStage } from "@/lib/schemas/deployment";
import type { ProjectId } from "@/lib/schemas";
import { cn } from "@/lib/utils";

const ACTION_LABEL: Record<string, string> = {
  create_pipeline: "Create pipeline",
  run_pipeline: "Run pipeline",
  direct_apply: "Apply to cluster",
};

/** The badge each state earns. `approved` is deliberately NOT success-coloured — it
 *  has not run yet, and a green tick there is a lie the eye believes before the text. */
function statusChip(d: DeploymentRequest) {
  if (d.approvalStatus === "rejected") {
    return { label: "Rejected", cls: "bg-muted text-muted-foreground border-border", Icon: XCircle };
  }
  if (d.approvalStatus === "pending") {
    return { label: "Awaiting approval", cls: "bg-warning/15 text-warning border-warning/30", Icon: Clock };
  }
  switch (d.executionStatus) {
    case "not_started":
      return { label: "Approved — not yet run", cls: "bg-sky-500/15 text-sky-500 border-sky-500/30", Icon: ShieldAlert };
    case "running":
      return { label: "Running", cls: "bg-sky-500/15 text-sky-500 border-sky-500/30", Icon: Loader2 };
    case "succeeded":
      return { label: "Succeeded", cls: "bg-success/15 text-success border-success/30", Icon: CheckCircle2 };
    case "canceled":
      return { label: "Canceled", cls: "bg-muted text-muted-foreground border-border", Icon: XCircle };
    default:
      return { label: d.executionStatus === "error" ? "Error" : "Failed", cls: "bg-destructive/15 text-destructive border-destructive/30", Icon: AlertTriangle };
  }
}

function describeRequest(d: DeploymentRequest): string {
  const r = d.request ?? {};
  if (d.action === "run_pipeline") {
    const pid = r["pipeline_id"];
    const branch = r["branch"];
    return [pid ? `pipeline ${String(pid)}` : null, branch ? `branch ${String(branch)}` : null]
      .filter(Boolean).join(" · ") || "—";
  }
  if (d.action === "create_pipeline") {
    return [r["name"] ? String(r["name"]) : null, r["yaml_path"] ? String(r["yaml_path"]) : null]
      .filter(Boolean).join(" · ") || "—";
  }
  return "—";
}

function failedStages(d: DeploymentRequest): FailedStage[] {
  const raw = (d.outcome ?? {})["failed_stages"];
  return Array.isArray(raw) ? (raw as FailedStage[]) : [];
}

export function DeploymentApprovals({ projectId }: { projectId: ProjectId }) {
  const qc = useQueryClient();
  const [error, setError] = React.useState<string | null>(null);
  const [busyId, setBusyId] = React.useState<string | null>(null);

  const q = useQuery({
    queryKey: ["deployments", projectId],
    queryFn: () => listDeployments(projectId),
    // Something is in flight — follow it without making the user click.
    refetchInterval: (query) =>
      (query.state.data ?? []).some((d) => d.executionStatus === "running") ? 8000 : false,
  });

  const invalidate = () => qc.invalidateQueries({ queryKey: ["deployments", projectId] });

  const run = (fn: () => Promise<unknown>, id: string) => {
    setError(null);
    setBusyId(id);
    fn()
      // The gate's refusals are the useful part: self_approval, not_approved,
      // already_executed. Surface the message rather than a generic failure.
      .catch((e: unknown) => setError(e instanceof Error ? e.message : "Something went wrong."))
      .finally(() => {
        setBusyId(null);
        invalidate();
      });
  };

  if (q.isLoading) return <LoadingState variant="card" />;
  if (q.isError) {
    return <ErrorState title="Could not load deployments"
      description={q.error instanceof Error ? q.error.message : "Unknown error."}
      onRetry={() => q.refetch()} />;
  }

  const rows = q.data ?? [];
  const pending = rows.filter((d) => d.approvalStatus === "pending");

  return (
    <div className="space-y-4">
      {error ? (
        <div className="border-destructive/30 bg-destructive/10 text-destructive flex items-start gap-2 rounded-lg border p-3 text-sm">
          <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden />
          <p className="min-w-0">{error}</p>
        </div>
      ) : null}

      {pending.length > 0 ? (
        <div className="border-warning/30 bg-warning/10 flex items-start gap-2 rounded-lg border p-3 text-sm">
          <Clock className="text-warning mt-0.5 size-4 shrink-0" aria-hidden />
          <p className="min-w-0">
            <span className="font-medium">
              {pending.length} deployment{pending.length === 1 ? "" : "s"} awaiting approval.
            </span>{" "}
            Nothing has run. A request cannot be approved by the person who raised it.
          </p>
        </div>
      ) : null}

      {rows.length === 0 ? (
        <EmptyState
          icon={PlayCircle}
          title="No deployment requests"
          description="When the agent asks to create or run a pipeline, the request appears here for approval. Generating deployment files needs no approval — only what reaches an environment does."
        />
      ) : null}

      <ul className="space-y-3">
        {rows.map((d) => {
          const chip = statusChip(d);
          const stages = failedStages(d);
          const busy = busyId === d.id;
          const outcomeDetail = typeof (d.outcome ?? {})["detail"] === "string"
            ? String((d.outcome ?? {})["detail"]) : null;
          const startedUnknown = (d.outcome ?? {})["started_unknown"] === true;

          return (
            <li key={d.id} className="bg-card rounded-lg border p-3">
              <div className="flex flex-wrap items-center gap-2">
                <Badge variant="outline" className={cn("gap-1", chip.cls)}>
                  <chip.Icon className={cn("size-3", d.executionStatus === "running" && d.approvalStatus === "approved" && "animate-spin")} aria-hidden />
                  {chip.label}
                </Badge>
                <span className="text-sm font-medium">{ACTION_LABEL[d.action] ?? d.action}</span>
                <span className="text-muted-foreground text-xs">{describeRequest(d)}</span>
                <Badge variant="outline" className="ml-auto text-xs">{d.environment}</Badge>
              </div>

              <p className="text-muted-foreground mt-2 text-xs">
                Requested by <span className="font-medium">{d.requestedBy}</span>
                {d.approvedBy ? (
                  <> · {d.approvalStatus === "rejected" ? "rejected" : "approved"} by{" "}
                    <span className="font-medium">{d.approvedBy}</span></>
                ) : null}
                {d.rejectionReason ? <> · {d.rejectionReason}</> : null}
              </p>

              {startedUnknown ? (
                <p className="border-warning/30 bg-warning/10 mt-2 rounded border p-2 text-xs">
                  <span className="font-medium">It is not known whether this started.</span>{" "}
                  The call to Azure DevOps failed, but the request may have been received
                  and only the reply lost. Check Azure DevOps before retrying — redeploying
                  on top of a running deployment is worse than waiting.
                </p>
              ) : outcomeDetail ? (
                <p className="text-muted-foreground mt-2 text-xs">{outcomeDetail}</p>
              ) : null}

              {stages.length > 0 ? (
                <div className="border-destructive/30 bg-destructive/5 mt-2 rounded border p-2">
                  <p className="text-destructive text-xs font-medium">
                    Failed at: {stages.map((s) => s.name ?? "?").join(", ")}
                  </p>
                  {stages.flatMap((s) => s.issues ?? []).slice(0, 4).map((i, n) => (
                    <p key={n} className="text-muted-foreground mt-1 font-mono text-[11px]">
                      {i.message}
                    </p>
                  ))}
                </div>
              ) : null}

              <div className="mt-3 flex flex-wrap items-center gap-2">
                {d.approvalStatus === "pending" ? (
                  <RequireRole
                    capability="run:approve"
                    fallback={<span className="text-muted-foreground text-xs">
                      You do not hold deployment approval on this project.
                    </span>}
                  >
                    <Button size="sm" disabled={busy}
                      onClick={() => run(() => approveDeployment(projectId, d.id), d.id)}>
                      {busy ? <Loader2 className="size-4 animate-spin" aria-hidden /> : <CheckCircle2 className="size-4" aria-hidden />}
                      Approve
                    </Button>
                    <Button size="sm" variant="outline" disabled={busy}
                      onClick={() => run(() => rejectDeployment(projectId, d.id), d.id)}>
                      <XCircle className="size-4" aria-hidden />
                      Reject
                    </Button>
                  </RequireRole>
                ) : null}

                {d.approvalStatus === "approved" && d.executionStatus === "not_started" ? (
                  <RequireRole
                    capability="run:trigger"
                    fallback={<span className="text-muted-foreground text-xs">
                      Approved. Someone with deployment approval has to run it.
                    </span>}
                  >
                    <Button size="sm" disabled={busy}
                      onClick={() => run(() => executeDeployment(projectId, d.id), d.id)}>
                      {busy ? <Loader2 className="size-4 animate-spin" aria-hidden /> : <PlayCircle className="size-4" aria-hidden />}
                      Deploy now
                    </Button>
                    <span className="text-muted-foreground text-xs">
                      Approved, but nothing has run yet.
                    </span>
                  </RequireRole>
                ) : null}

                {d.executionStatus === "running" ? (
                  <Button size="sm" variant="outline" disabled={busy}
                    onClick={() => run(() => refreshDeployment(projectId, d.id), d.id)}>
                    <RotateCw className={cn("size-4", busy && "animate-spin")} aria-hidden />
                    Check status
                  </Button>
                ) : null}

                {d.externalUrl ? (
                  <a href={d.externalUrl} target="_blank" rel="noreferrer"
                    className="text-primary text-xs underline underline-offset-2">
                    View the run in Azure DevOps
                  </a>
                ) : null}
              </div>
            </li>
          );
        })}
      </ul>

      <Button size="sm" variant="ghost" onClick={() => q.refetch()} disabled={q.isFetching}>
        <RefreshCw className={cn("size-4", q.isFetching && "animate-spin")} aria-hidden />
        Refresh
      </Button>
    </div>
  );
}
