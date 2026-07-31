"use client";

import * as React from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  AlertTriangle,
  CheckCircle2,
  Clock,
  FolderOpen,
  Plug,
  Play,
  Puzzle,
  Settings,
  ShieldAlert,
  XCircle,
} from "lucide-react";
import { formatDistanceToNow } from "date-fns";

import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { LoadingState } from "@/components/ui/loading-state";
import { Skeleton } from "@/components/ui/skeleton";
import { StatusBadge } from "@/components/ui/status-badge";
import { Separator } from "@/components/ui/separator";
import { CostMeter } from "@/components/app/cost-meter";
import { ModelSelector } from "@/components/app/model-selector";
import { PhasePipeline } from "@/components/app/phase-pipeline";
import { DeliveryStatusPicker } from "@/components/app/delivery-status-badge";
import { ProjectRunsTable } from "@/components/app/project-runs-table";
import { RunTriggerDialog } from "@/components/runs/run-trigger-dialog";
import { RequireRole } from "@/components/auth/require-role";
import { OutOfScope } from "@/components/auth/scope-empty-state";
import { ApiRequestError } from "@/lib/api/client";
import { listArtifacts } from "@/lib/api/artifacts";
import { listConnectors } from "@/lib/api/connectors";
import { getProject, updateProject } from "@/lib/api/projects";
import { useRawSession } from "@/components/auth/session-provider";
import { effectivePlatformRole } from "@/lib/auth/effective-role";
import {
  PROJECT_DELIVERY_STATUS_LABEL,
  type ProjectDeliveryStatus,
} from "@/lib/schemas/project";
import { listRuns } from "@/lib/api/runs";
import { getProjectCostSummary } from "@/lib/api/traces";
import { qk } from "@/lib/api/query-keys";
import { phaseHref, ROUTABLE_PHASES } from "@/lib/agents";
import type { Artifact, Connector, Project, ProjectId, Run } from "@/lib/schemas";

const TEMPLATE_LABEL: Record<Project["template"], string> = {
  web_app: "Web app",
  microservice: "Microservice",
  data_pipeline: "Data pipeline",
  blank: "Blank",
};

export default function ProjectOverviewPage() {
  const params = useParams<{ id: string }>();
  const id = params.id as ProjectId;
  const router = useRouter();
  const [runOpen, setRunOpen] = React.useState(false);
  // Project-level model choice (an offering id = provider connection + model). Local
  // for now; it pre-selects the offering in the Run dialog. TODO(byok-model): persist
  // as Project.default_offering_id (Phase 3).
  const [projectOffering, setProjectOffering] = React.useState<string>();
  const session = useRawSession();
  const queryClient = useQueryClient();

  const projectQ = useQuery({
    queryKey: qk.projects.detail(id),
    queryFn: () => getProject(id),
  });

  // Delivery status is the Project Admin's to set, with the two tiers above
  // them able to override it in their own scope (the API enforces the same
  // set — app/api/projects/[id]/route.ts).
  const role = effectivePlatformRole(session);
  const canSetDeliveryStatus =
    role === "project_admin" || role === "bu_admin" || role === "org_admin";

  const deliveryStatusMutation = useMutation({
    mutationFn: (deliveryStatus: ProjectDeliveryStatus) => updateProject(id, { deliveryStatus }),
    onSuccess: (p) => {
      toast.success(`Status set to ${PROJECT_DELIVERY_STATUS_LABEL[p.deliveryStatus]}`);
      queryClient.invalidateQueries({ queryKey: qk.projects.detail(id) });
      queryClient.invalidateQueries({ queryKey: qk.projects.all() });
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : "Couldn't update status"),
  });

  const runsQ = useQuery({
    queryKey: qk.runs.forProject(id),
    queryFn: () => listRuns({ projectId: id, pageSize: 50 }),
  });

  const artifactsQ = useQuery({
    queryKey: qk.artifacts.forProject(id),
    queryFn: () => listArtifacts(id),
  });

  const connectorsQ = useQuery({
    queryKey: qk.connectors.list(),
    queryFn: () => listConnectors(),
  });

  // Project LLM spend + tokens (last 7 days), Langfuse-sourced via /traces.
  const costSummaryQ = useQuery({
    queryKey: qk.traces.projectSummary(id, 7),
    queryFn: () => getProjectCostSummary(id, 7),
  });

  if (projectQ.isLoading) {
    return (
      <div className="w-full space-y-6 p-4 md:px-10 md:py-8">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-12 w-full" />
        <LoadingState variant="card" />
      </div>
    );
  }

  // An unauthorized project answers 404 from the API (deliberately
  // indistinguishable from a missing one — see app/api/projects/[id]/route.ts),
  // so a 404 here means "not yours or not real" and deserves the out-of-scope
  // state with a way back, not a retry button that will never succeed. Other
  // failures keep the retry, because those genuinely are transient.
  const notFound =
    projectQ.isError &&
    projectQ.error instanceof ApiRequestError &&
    projectQ.error.status === 404;

  if (notFound) return <OutOfScope kind="project" />;

  if (projectQ.isError || !projectQ.data) {
    return (
      <div className="w-full p-4 md:px-10 md:py-8">
        <ErrorState
          title="Couldn't load this project"
          description={
            projectQ.error instanceof Error
              ? projectQ.error.message
              : "Something went wrong loading this project."
          }
          onRetry={() => projectQ.refetch()}
        />
      </div>
    );
  }

  const project = projectQ.data;
  // A project a Project Admin created stays pending until its BU Admin
  // approves it (governance approval, not an agent gate) — pipeline/agent
  // actions are disabled until then, and a rejected project can't run at all.
  const isPendingApproval = project.approvalStatus === "pending_approval";
  const isRejected = project.approvalStatus === "rejected";
  const actionsLocked = isPendingApproval || isRejected;
  const runs = runsQ.data?.items ?? [];
  const artifacts = artifactsQ.data ?? [];

  const recentRuns = runs.slice(0, 10);
  const blockers = runs.filter((r) => r.status === "awaiting_approval");
  const recentArtifacts = [...artifacts]
    .sort((a, b) => b.updatedAt.localeCompare(a.updatedAt))
    .slice(0, 8);

  // Project spend + tokens (last 7 days): prefer the Langfuse-sourced summary; fall
  // back to run-derived aggregation if it hasn't loaded (or tracing is disabled).
  const weekAgo = Date.now() - 7 * 24 * 3600 * 1000;
  const summary = costSummaryQ.data;
  const weeklyCost = summary
    ? {
        usd: summary.totalCostUsd,
        inputTokens: summary.inputTokens,
        outputTokens: summary.outputTokens,
      }
    : runs
        .filter((r) => new Date(r.startedAt).getTime() >= weekAgo)
        .reduce(
          (acc, r) => ({
            usd: acc.usd + r.cost.usd,
            inputTokens: acc.inputTokens + r.cost.inputTokens,
            outputTokens: acc.outputTokens + r.cost.outputTokens,
          }),
          { usd: 0, inputTokens: 0, outputTokens: 0 },
        );
  const weeklyCostSeries = groupCostByDay(runs, 7);

  return (
    <div className="w-full space-y-6 p-4 md:px-10 md:py-8">
      <RunTriggerDialog
        projectId={project.id}
        open={runOpen}
        onOpenChange={setRunOpen}
        initialOffering={projectOffering}
      />

      {/* Project name, delivery track and description live in the project
          layout (PRD §32.1) — this row is the overview's actions only. */}
      <header className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant="secondary">{TEMPLATE_LABEL[project.template]}</Badge>
          <DeliveryStatusPicker
            status={project.deliveryStatus}
            canEdit={canSetDeliveryStatus}
            busy={deliveryStatusMutation.isPending}
            onChange={(next) => deliveryStatusMutation.mutate(next)}
          />
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <div className="flex items-center gap-1.5">
            <span className="text-muted-foreground hidden font-mono text-[10px] tracking-[0.12em] uppercase sm:inline">
              Model
            </span>
            <ModelSelector
              aria-label="Project agent model"
              value={projectOffering}
              onValueChange={setProjectOffering}
            />
          </div>
          <RequireRole capability="run:trigger">
            <Button onClick={() => setRunOpen(true)} disabled={actionsLocked}>
              <Play />
              Run agent
            </Button>
          </RequireRole>
          <Button
            variant="outline"
            onClick={() => router.push(`/projects/${project.id}/capabilities`)}
          >
            <Puzzle />
            Capabilities
          </Button>
          <Button variant="outline" onClick={() => router.push(`/projects/${project.id}/settings`)}>
            <Settings />
            Settings
          </Button>
        </div>
      </header>

      {/* ─── Pending / rejected governance banner ────────── */}
      {actionsLocked && (
        <div
          className={cn(
            "flex items-start gap-3 rounded-xl border px-4 py-3",
            isPendingApproval
              ? "border-warning/30 bg-warning/10"
              : "border-destructive/30 bg-destructive/10",
          )}
        >
          {isPendingApproval ? (
            <Clock className="text-warning mt-0.5 size-4 shrink-0" aria-hidden />
          ) : (
            <XCircle className="text-destructive mt-0.5 size-4 shrink-0" aria-hidden />
          )}
          <div className="min-w-0 space-y-0.5">
            <p className="text-[13px] font-medium">
              {isPendingApproval
                ? "Waiting on Business Unit Admin approval"
                : "Project creation was rejected"}
            </p>
            <p className="text-muted-foreground text-[12.5px] leading-relaxed">
              {isPendingApproval
                ? "This project was created by a Project Admin and needs sign-off before its pipeline can run. It'll appear in the approving BU Admin's approval queue."
                : project.approvalReason
                  ? `Reason: "${project.approvalReason}"`
                  : "The approving BU Admin rejected this project. Agent runs are disabled."}
            </p>
          </div>
        </div>
      )}

      {/* ─── Phase pipeline ───────────────────────────────── */}
      <PhasePipeline
        pipeline={project.pipeline}
        track={project.track}
        hrefFor={(p) =>
          !actionsLocked && ROUTABLE_PHASES.has(p) ? phaseHref(project.id, p) : undefined
        }
      />

      {/* ─── Main grid ────────────────────────────────────── */}
      <div className="grid gap-6 lg:grid-cols-3">
        {/* Main column */}
        <div className="space-y-6 lg:col-span-2">
          {/* Recent runs */}
          <section aria-labelledby="runs-heading" className="space-y-3">
            <div className="flex items-baseline justify-between">
              <h2 id="runs-heading" className="text-lg font-semibold tracking-tight">
                Recent runs
              </h2>
              {runs.length > recentRuns.length && (
                <Button variant="link" size="sm" asChild className="h-auto p-0">
                  <Link href={`/runs?projectId=${project.id}`}>View all {runs.length}</Link>
                </Button>
              )}
            </div>
            {runsQ.isError ? (
              <ErrorState
                title="Couldn't load runs"
                description={
                  runsQ.error instanceof Error ? runsQ.error.message : "Unknown error."
                }
                onRetry={() => runsQ.refetch()}
              />
            ) : (
              <ProjectRunsTable runs={runsQ.isLoading ? null : recentRuns} />
            )}
          </section>

          {/* Recent artifacts */}
          <section aria-labelledby="artifacts-heading" className="space-y-3">
            <div className="flex items-baseline justify-between">
              <h2 id="artifacts-heading" className="text-lg font-semibold tracking-tight">
                Recent artifacts
              </h2>
            </div>
            <RecentArtifactsList
              projectId={project.id}
              artifacts={artifactsQ.isLoading ? null : recentArtifacts}
            />
          </section>
        </div>

        {/* Right rail */}
        <aside className="space-y-4">
          {/* Blockers */}
          <BlockersPanel blockers={blockers} isLoading={runsQ.isLoading} />

          {/* Cost this week */}
          <section aria-labelledby="cost-heading" className="space-y-2">
            <h2
              id="cost-heading"
              className="text-muted-foreground text-xs font-semibold uppercase tracking-wider"
            >
              Cost this week
            </h2>
            <CostMeter cost={weeklyCost} series={weeklyCostSeries} />
          </section>

          {/* Connectors */}
          <ConnectorsPanel
            connectors={connectorsQ.data ?? null}
            isLoading={connectorsQ.isLoading}
          />

          {/* Guardrails placeholder — wired to SSE in Chunk 15 */}
          <section aria-labelledby="guardrails-heading" className="space-y-2">
            <h2
              id="guardrails-heading"
              className="text-muted-foreground text-xs font-semibold uppercase tracking-wider"
            >
              Guardrail hits (7d)
            </h2>
            <Card>
              <CardContent className="flex items-center gap-3 p-4">
                <ShieldAlert className="text-muted-foreground size-5" aria-hidden />
                <div className="flex flex-col">
                  <span className="text-xl font-semibold">0</span>
                  <span className="text-muted-foreground text-xs">
                    PII + injection + policy
                  </span>
                </div>
              </CardContent>
            </Card>
          </section>
        </aside>
      </div>

      {/* Empty overview tips if nothing has happened */}
      {runs.length === 0 && artifacts.length === 0 && (
        <EmptyState
          icon={FolderOpen}
          title="This project is empty"
          description="Trigger your first agent to generate requirements, design, or code."
          action={
            <RequireRole capability="run:trigger" fallback={<Button disabled>Run agent</Button>}>
              {actionsLocked ? (
                <Button disabled>Run Requirements agent</Button>
              ) : (
                <Button asChild>
                  <Link href={`/projects/${project.id}/requirements`}>Run Requirements agent</Link>
                </Button>
              )}
            </RequireRole>
          }
          secondaryAction={
            <Button variant="outline" asChild>
              <Link href="/integrations">Connect tools</Link>
            </Button>
          }
        />
      )}
    </div>
  );
}

// ───────────────────────────────────────────────────────────
//  Right-rail sub-components
// ───────────────────────────────────────────────────────────

function BlockersPanel({
  blockers,
  isLoading,
}: {
  blockers: Run[];
  isLoading?: boolean;
}) {
  return (
    <section aria-labelledby="blockers-heading" className="space-y-2">
      <div className="flex items-baseline justify-between">
        <h2
          id="blockers-heading"
          className="text-muted-foreground text-xs font-semibold uppercase tracking-wider"
        >
          Awaiting approval
        </h2>
        {blockers.length > 0 && (
          <Badge variant="warning" className="h-5 px-2 text-[10px]">
            {blockers.length}
          </Badge>
        )}
      </div>
      <Card>
        <CardContent className="p-3">
          {isLoading ? (
            <div className="space-y-2">
              <Skeleton className="h-10 w-full" />
              <Skeleton className="h-10 w-full" />
            </div>
          ) : blockers.length === 0 ? (
            <div className="text-muted-foreground flex items-center gap-2 p-2 text-sm">
              <CheckCircle2 className="text-success size-4" aria-hidden />
              Nothing waiting on you.
            </div>
          ) : (
            <ul className="space-y-1">
              {blockers.slice(0, 5).map((r) => (
                <li key={r.id}>
                  <Link
                    href={`/runs/${r.id}`}
                    className={cn(
                      "flex items-start gap-2 rounded-md border p-2 text-sm transition-colors",
                      "hover:bg-accent hover:text-accent-foreground",
                    )}
                  >
                    <AlertTriangle
                      className="text-warning mt-0.5 size-4 shrink-0"
                      aria-hidden
                    />
                    <div className="min-w-0 flex-1">
                      <p className="truncate font-medium">{r.title}</p>
                      <p className="text-muted-foreground flex items-center gap-2 text-xs">
                        <Clock className="size-3" aria-hidden />
                        {formatDistanceToNow(new Date(r.startedAt), { addSuffix: true })}
                      </p>
                    </div>
                    <StatusBadge status={r.status} iconOnly />
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
    </section>
  );
}

function RecentArtifactsList({
  projectId,
  artifacts,
}: {
  projectId: ProjectId;
  artifacts: Artifact[] | null;
}) {
  if (artifacts === null) return <LoadingState variant="list" rows={4} />;
  if (artifacts.length === 0) {
    return (
      <EmptyState
        title="No artifacts yet"
        description="Run an agent to generate stories, designs, code, or tests."
        variant="card"
      />
    );
  }
  return (
    <Card>
      <CardContent className="p-2">
        <ul className="divide-y">
          {artifacts.map((a) => (
            <li key={a.id}>
              <Link
                href={`/projects/${projectId}/${a.phase}?artifact=${a.id}`}
                className="hover:bg-accent flex items-center gap-3 rounded-md px-2 py-2 transition-colors"
              >
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium">{a.title}</p>
                  <div className="text-muted-foreground flex items-center gap-2 text-xs">
                    <span className="font-mono uppercase tracking-wider">
                      {a.type.replace(/_/g, " ")}
                    </span>
                    <Separator orientation="vertical" className="h-3" />
                    <span>v{a.version}</span>
                    <Separator orientation="vertical" className="h-3" />
                    <span>
                      {formatDistanceToNow(new Date(a.updatedAt), { addSuffix: true })}
                    </span>
                  </div>
                </div>
                <StatusBadge status={a.status} />
              </Link>
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}

function ConnectorsPanel({
  connectors,
  isLoading,
}: {
  connectors: Connector[] | null;
  isLoading?: boolean;
}) {
  return (
    <section aria-labelledby="connectors-heading" className="space-y-2">
      <div className="flex items-baseline justify-between">
        <h2
          id="connectors-heading"
          className="text-muted-foreground text-xs font-semibold uppercase tracking-wider"
        >
          Connectors
        </h2>
        <Button variant="link" size="sm" asChild className="h-auto p-0 text-xs">
          <Link href="/integrations">Manage</Link>
        </Button>
      </div>
      <Card>
        <CardContent className="p-3">
          {isLoading || connectors === null ? (
            <div className="space-y-2">
              <Skeleton className="h-6 w-full" />
              <Skeleton className="h-6 w-full" />
              <Skeleton className="h-6 w-full" />
            </div>
          ) : connectors.filter((c) => c.installed).length === 0 ? (
            <div className="flex flex-col items-start gap-2 p-2">
              <p className="text-muted-foreground text-sm">No connectors installed.</p>
              <Button variant="outline" size="sm" asChild>
                <Link href="/integrations">
                  <Plug className="size-3.5" />
                  Connect a tool
                </Link>
              </Button>
            </div>
          ) : (
            <ul className="space-y-1.5">
              {connectors
                .filter((c) => c.installed)
                .map((c) => (
                  <li
                    key={c.id}
                    className="flex items-center justify-between gap-2 text-sm"
                  >
                    <span className="truncate">{c.name}</span>
                    <HealthDot health={c.health} />
                  </li>
                ))}
            </ul>
          )}
        </CardContent>
      </Card>
    </section>
  );
}

function HealthDot({ health }: { health: Connector["health"] }) {
  const tone =
    health === "healthy"
      ? "bg-success"
      : health === "degraded"
        ? "bg-warning"
        : "bg-destructive";
  return (
    <span className="flex items-center gap-1.5">
      <span
        className={cn("inline-block size-2 rounded-full", tone)}
        aria-label={`Connector ${health}`}
      />
      <span className="text-muted-foreground text-xs capitalize">{health}</span>
    </span>
  );
}

// Helpers ---------------------------------------------------

function groupCostByDay(runs: readonly Run[], days: number): number[] {
  const buckets = new Array(days).fill(0);
  const now = Date.now();
  const dayMs = 24 * 3600 * 1000;
  for (const r of runs) {
    const ago = now - new Date(r.startedAt).getTime();
    const idx = days - 1 - Math.floor(ago / dayMs);
    if (idx < 0 || idx >= days) continue;
    buckets[idx] += r.cost.usd;
  }
  // Cumulative for a nicer sparkline shape
  for (let i = 1; i < buckets.length; i++) buckets[i] += buckets[i - 1];
  return buckets;
}
