"use client";

import * as React from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  Clock,
  FolderOpen,
  Plug,
  Play,
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
import { ModelSelector } from "@/components/app/model-selector";
import { PhasePipeline } from "@/components/app/phase-pipeline";
import { DeliveryStatusPicker } from "@/components/app/delivery-status-badge";
import { ProjectCostPanel } from "@/components/app/project-cost-panel";
import { ProjectRunsTable } from "@/components/app/project-runs-table";
import { RunTriggerDialog } from "@/components/runs/run-trigger-dialog";
import { isStoredArtifact } from "@/components/app/artifact-list";
import { RequireRole } from "@/components/auth/require-role";
import { OutOfScope } from "@/components/auth/scope-empty-state";
import { ApiRequestError } from "@/lib/api/client";
import { listArtifacts } from "@/lib/api/artifacts";
import { listConnectors } from "@/lib/api/connectors";
import { connectorStatusLabel } from "@/lib/connectors";
import { getProject, updateProject } from "@/lib/api/projects";
import { useCanSeeProjectCost } from "@/hooks/use-can-see-project-cost";
import { useRawSession } from "@/components/auth/session-provider";
import { effectivePlatformRole } from "@/lib/auth/effective-role";
import {
  PROJECT_DELIVERY_STATUS_LABEL,
  type ProjectDeliveryStatus,
} from "@/lib/schemas/project";
import { listRuns } from "@/lib/api/runs";
import { qk } from "@/lib/api/query-keys";
import { BUILT_AGENTS, PHASE_LABEL, phaseHref, ROUTABLE_PHASES } from "@/lib/agents";
import { tileStateFor } from "@/lib/agent-access";
import { ROLE_META } from "@/lib/roles";
import { RequestAccessButton } from "@/components/requests/request-access-button";
import { RequestAgentAccessDialog } from "@/components/app/request-agent-access-dialog";
import { canRaiseType } from "@/lib/requests/routing";
import { BUSINESS_UNIT_LABEL } from "@/lib/scope";
import { useScopedBusinessUnits } from "@/hooks/use-scoped-business-units";
import type { Artifact, Connector, Project, ProjectId } from "@/lib/schemas";
import type { Phase } from "@/lib/schemas/enums";

const TEMPLATE_LABEL: Record<Project["template"], string> = {
  web_app: "Web app",
  microservice: "Microservice",
  data_pipeline: "Data pipeline",
  blank: "Blank",
};

export default function ProjectOverviewPage() {
  const params = useParams<{ id: string }>();
  const id = params.id as ProjectId;
  const [runOpen, setRunOpen] = React.useState(false);
  // Project-level model choice (an offering id = provider connection + model). Local
  // for now; it pre-selects the offering in the Run dialog. TODO(byok-model): persist
  // as Project.default_offering_id (Phase 3).
  const [projectOffering, setProjectOffering] = React.useState<string>();
  const session = useRawSession();
  const canSeeCost = useCanSeeProjectCost(id);
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

  // Only contributors ask. A Project Admin owns every agent already, and the
  // two governance tiers hold none by design (PRD §14.8) — offering them a
  // request would suggest that decision is negotiable.
  const canRequestAgentAccess = canRaiseType(role, "agent_access");
  const viewerRole = role;

  /**
   * Per-phase tile state for the pipeline (`lib/agent-access.ts`) — replaces
   * the old locked/unlocked-only split with the 4-state owner/use/locked/
   * coming_soon model (multi-track-agent-access-design.md §2.2).
   *
   * Unlike the split this replaces, this does not special-case a role that
   * reaches nothing at all (e.g. the governance tier, PRD §14.8) to hide its
   * locks — with `builtAgents` hardcoded empty below, every phase currently
   * resolves to `coming_soon` regardless of role, which happens to keep a
   * governance viewer's pipeline free of padlocks for now. That stops being
   * true the moment `builtAgents` reflects real data, at which point a
   * zero-reach role would see every phase as `locked` — worth revisiting
   * when that TODO is picked up.
   */
  const tileState = React.useMemo(() => {
    const track = projectQ.data?.track;
    if (!viewerRole || !track) return undefined;
    // TODO(next task): read the project's actually-verified agent list once that
    // signal exists server-side. Until then BUILT_AGENTS is the single source of
    // truth, shared with each agent's own standalone page gate — see lib/agents.ts
    // for which agents are on it and what "verified" meant for each. Agents not on
    // it keep their old, unverified code per the design doc's "assume broken until
    // properly rebuilt" framing (multi-track-agent-access-design.md).
    const builtAgents: readonly Phase[] = BUILT_AGENTS;
    return (phase: Phase) => tileStateFor(viewerRole, phase, track, builtAgents);
  }, [viewerRole, projectQ.data?.track]);

  // The unit's real name, not the word "Business Unit" — the request names the
  // scope it is being raised in, and a label there says nothing.
  const { units: scopedUnits } = useScopedBusinessUnits();

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
  // Falls back to the generic label rather than an empty string: a viewer bound
  // to no unit still sees a sentence that reads.
  const projectUnitName =
    scopedUnits.find((u) => String(u.id) === String(project.workspaceId))?.name ??
    BUSINESS_UNIT_LABEL;
  const runs = runsQ.data?.items ?? [];
  const artifacts = artifactsQ.data ?? [];

  const recentRuns = runs.slice(0, 10);

  // FILES THE PROJECT PRODUCED, not board rows. `listArtifacts` with no phase filter
  // also returns the SYNTHESISED story artifacts materialised from a run's
  // requirements_payload, and a board pull yields one per work item — all sharing the
  // ingest run's timestamp. Fifteen of those buried the project's only real document
  // below the fold, so this panel showed no artifacts at all while being full of rows.
  //
  // The test is the id shape (`isStoredArtifact`): a stored artifact's id is a UUID, a
  // synthesised one is `{run_id}:story:{key}`. That corrects itself if stories ever get
  // real rows. Stories have their own screen and their own count.
  const recentArtifacts = artifacts
    .filter((a) => isStoredArtifact(a.id))
    .sort((a, b) => b.updatedAt.localeCompare(a.updatedAt))
    .slice(0, 8);

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
              projectId={id}
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
          {/* Raised from here rather than the Requests page because this is
              where someone discovers the agent they need is closed to them.
              Renders nothing when their role already reaches every agent this
              project runs. */}
          {canRequestAgentAccess && (
            <RequestAgentAccessDialog
              projectId={String(project.id)}
              projectName={project.name}
              workspaceId={String(project.workspaceId ?? "")}
              workspaceName={projectUnitName}
              phases={project.pipeline.map((p) => p.phase)}
              requesterRole={role}
            />
          )}
          {/* No Settings button. The tab bar directly above already carries
              one, and two controls three inches apart going to the same page
              is not two ways in — it is one destination the reader has to
              decide between. The tab is the one that shows where you are. */}
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
        tileState={tileState}
        renderLockedAction={(p) => (
          <RequestAccessButton
            label="Request access"
            className="h-7"
            prefill={{
              type: "agent_access",
              title: `${PHASE_LABEL[p]} agent access`,
              description: `Requesting the ${PHASE_LABEL[p]} agent on ${project.name}. My role (${viewerRole ? ROLE_META[viewerRole].label : "current role"}) doesn't reach it.`,
              projectId: project.id,
              workspaceId: project.workspaceId ? String(project.workspaceId) : undefined,
            }}
          />
        )}
      />

      {/* ─── Main grid ────────────────────────────────────── */}
      <div className="grid gap-6 lg:grid-cols-3">
        {/* Main column */}
        <div className="space-y-6 lg:col-span-2">
          {/* Spend leads the column: it is the only thing here that reports on
              the project as a whole rather than listing its latest events, and
              it is now the page's single cost reading. In the two-thirds column
              rather than full width — the chart scales to its container, and
              six single-series bars spread across the whole page is ~14% ink
              and 86% background, which reads as an empty block.

              Behind the same gate as the Cost tab and page. Restricting those
              two while leaving the month's spend plotted on the landing page
              would not be a restriction at all. */}
          {canSeeCost && (
            <ProjectCostPanel
              projectId={project.id}
              projectName={project.name}
              track={project.track}
              monthlyBudgetUsd={project.monthlyBudgetUsd ?? null}
              monthlySpendUsd={project.monthlySpendUsd ?? 0}
            />
          )}

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
          {/* The "Awaiting approval" panel used to lead this rail. It restated
              what Recent runs already shows — the same runs, with the same
              "Awaiting approval" status badge, in the column beside it — and
              Approvals is a top-level destination that gathers them across
              every project. A per-project copy of a cross-project queue earns
              its space only by saying something the list beside it does not.

              "Cost this week" used to sit here too. Two cost readings on one page
              disagreed on their own terms — this tile summed per-run LLM cents
              to $0.192 while the panel above reports the project's $2,680 month
              — and a reader has no way to tell which is the project's spend.
              The panel answers the same question with the trend and the agent
              split beside it, so this was the one to go. It also took the
              trend badge's "258000%" with it: a week-over-week percentage off a
              near-zero base is arithmetic, not information. */}

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
        title="No documents yet"
        // Stories are deliberately not counted here — they are board rows, not files,
        // and they have their own screen. Promising them would explain an empty panel
        // with something that will never fill it.
        description="Ask an agent to generate a document, diagram, or deck."
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
  const label = connectorStatusLabel(health);
  return (
    <span className="flex items-center gap-1.5">
      <span
        className={cn("inline-block size-2 rounded-full", tone)}
        aria-label={`Connector ${label}`}
      />
      <span className="text-muted-foreground text-xs">{label}</span>
    </span>
  );
}

