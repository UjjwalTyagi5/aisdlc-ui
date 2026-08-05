"use client";

import * as React from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { toast } from "sonner";

import { PageTitle } from "@/components/app/page-title";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { LoadingState } from "@/components/ui/loading-state";
import { ApiErrorState } from "@/components/feedback/api-error-state";
import { EmptyProjects } from "@/components/feedback/empty-projects";
import { RequireRole } from "@/components/auth/require-role";
import { CreateProjectDialog } from "@/components/app/create-project-dialog";
import { ProjectCard } from "@/components/app/project-card";
import { SpendBreakdownCard } from "@/components/app/spend-breakdown-card";
import {
  ProjectsToolbar,
  type ProjectsToolbarState,
  type TemplateFilter,
} from "@/components/app/projects-toolbar";
import { useCan } from "@/hooks/use-can";
import { useWorkspaces } from "@/hooks/use-workspaces";
import { useAccessScope } from "@/hooks/use-access-scope";
import { ScopeChip } from "@/components/app/scope-indicator";
import { NoScopeAccess } from "@/components/auth/scope-empty-state";
import { archiveProject, listProjects, restoreProject } from "@/lib/api/projects";
import { qk } from "@/lib/api/query-keys";
import type { Project } from "@/lib/schemas";
import { BUSINESS_UNIT_LABEL, BUSINESS_UNIT_LABEL_PLURAL } from "@/lib/scope";

const PAGE_SIZE = 12;

export default function ProjectsPage() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const queryClient = useQueryClient();
  const canCreate = useCan("project:create");

  // Filters + view in URL so refresh preserves state.
  const toolbarState: ProjectsToolbarState = {
    search: searchParams.get("q") ?? "",
    template: (searchParams.get("template") as TemplateFilter) || "all",
    sort: (searchParams.get("sort") as ProjectsToolbarState["sort"]) || "recent",
    showArchived: searchParams.get("archived") === "1",
    view: (searchParams.get("view") as ProjectsToolbarState["view"]) || "grid",
  };
  const page = Number(searchParams.get("page") ?? "1");

  const updateParams = React.useCallback(
    (patch: Partial<ProjectsToolbarState> & { page?: number }) => {
      const next = new URLSearchParams(searchParams);
      const set = (key: string, value: string | number | boolean | undefined, def?: string) => {
        if (value === undefined || value === "" || value === false || String(value) === def) {
          next.delete(key);
        } else {
          next.set(key, String(value));
        }
      };
      if (patch.search !== undefined) set("q", patch.search);
      if (patch.template !== undefined) set("template", patch.template, "all");
      if (patch.sort !== undefined) set("sort", patch.sort, "recent");
      if (patch.showArchived !== undefined) set("archived", patch.showArchived ? "1" : "");
      if (patch.view !== undefined) set("view", patch.view, "grid");
      if (patch.page !== undefined) set("page", patch.page, "1");
      router.replace(`/projects?${next.toString()}`);
    },
    [router, searchParams],
  );

  // Create dialog state
  const createRequested = searchParams.get("new") === "1";
  const [dialogOpen, setDialogOpen] = React.useState(false);
  React.useEffect(() => {
    if (createRequested) setDialogOpen(true);
  }, [createRequested]);
  const closeDialog = (open: boolean) => {
    setDialogOpen(open);
    if (!open && createRequested) {
      const next = new URLSearchParams(searchParams);
      next.delete("new");
      router.replace(`/projects?${next.toString()}`);
    }
  };

  const filters = React.useMemo(
    () => ({
      search: toolbarState.search || undefined,
      archived: toolbarState.showArchived ? undefined : false,
      page,
      pageSize: PAGE_SIZE,
    }),
    [toolbarState.search, toolbarState.showArchived, page],
  );

  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey: qk.projects.list({ ...filters, template: toolbarState.template }),
    queryFn: () => listProjects(filters),
    placeholderData: (prev) => prev,
  });
  const { data: workspaces = [] } = useWorkspaces();
  const {
    scope,
    level,
    isOrgWide,
    managedProjectIds,
    projectIds,
  } = useAccessScope();

  // Client-side finishing moves: template filter + sort (server handles search/archive/page).
  const items = React.useMemo(() => {
    const base = data?.items ?? [];
    let next = base.slice();
    if (toolbarState.template !== "all") {
      next = next.filter((p) => p.template === toolbarState.template);
    }
    next.sort((a, b) => {
      if (toolbarState.sort === "name") return a.name.localeCompare(b.name);
      if (toolbarState.sort === "template") return a.template.localeCompare(b.template);
      return b.lastActivityAt.localeCompare(a.lastActivityAt);
    });
    return next;
  }, [data, toolbarState.template, toolbarState.sort]);

  // Group by Business Unit (PRD §12 scope hierarchy) — the org-wide project
  // count is meaningless on its own; which BU owns what is the real question.
  // Projects with no matching workspace fall into a trailing "Unassigned" group
  // rather than disappearing.
  const groups = React.useMemo(() => {
    const byId = new Map<string, string>(workspaces.map((w) => [String(w.id), w.displayName]));
    const buckets = new Map<string, { label: string; items: Project[] }>();
    for (const p of items) {
      const known = p.workspaceId ? byId.get(p.workspaceId) : undefined;
      const label = known ?? "Unassigned";
      const key = known ? p.workspaceId! : "__unassigned";
      if (!buckets.has(key)) buckets.set(key, { label, items: [] });
      buckets.get(key)!.items.push(p);
    }
    return Array.from(buckets.values()).sort((a, b) => {
      if (a.label === "Unassigned") return 1;
      if (b.label === "Unassigned") return -1;
      return a.label.localeCompare(b.label);
    });
  }, [items, workspaces]);

  const pagination = data?.pagination;
  const totalPages = pagination ? Math.max(1, Math.ceil(pagination.total / pagination.pageSize)) : 1;

  // The scope these projects were drawn from, named for the chip. A grouped list
  // already shows each Business Unit heading, so the chip carries the tier and
  // the count rather than repeating one unit's name.
  const scopeName = isOrgWide
    ? null
    : groups.length === 1
      ? groups[0]!.label
      : `${groups.length} ${groups.length === 1 ? BUSINESS_UNIT_LABEL.toLowerCase() : BUSINESS_UNIT_LABEL_PLURAL.toLowerCase()}`;

  // Resolved and bound to nothing: a person with no assignment yet needs a
  // different message from "no projects match your filter".
  const unbound = scope !== null && !isOrgWide && projectIds.length === 0;

  // Metric strip counts — derived from real listProjects data
  const activeCount = items.filter((p) => !p.archived).length;
  const archivedCount = items.filter((p) => p.archived).length;
  const runningCount = items.filter((p) =>
    p.pipeline.some((e) => e.status === "running"),
  ).length;
  const awaitingCount = items.filter((p) =>
    p.pipeline.some((e) => e.status === "awaiting_approval"),
  ).length;

  const archiveMutation = useMutation({
    mutationFn: archiveProject,
    onSuccess: (p) => {
      if (!p.archived) {
        // A BU Admin's archive request isn't applied immediately — it opens
        // a governance approval routed to the Org Admin instead.
        toast.info("Sent for approval", {
          description: `Your Org Admin needs to approve archiving "${p.name}".`,
        });
      } else {
        toast.success(`Archived "${p.name}"`);
      }
      queryClient.invalidateQueries({ queryKey: qk.projects.all() });
    },
    onError: (err) =>
      toast.error("Archive failed", {
        description: err instanceof Error ? err.message : undefined,
      }),
  });

  const restoreMutation = useMutation({
    mutationFn: restoreProject,
    onSuccess: (p) => {
      toast.success(`Restored "${p.name}"`);
      queryClient.invalidateQueries({ queryKey: qk.projects.all() });
    },
    onError: (err) =>
      toast.error("Restore failed", {
        description: err instanceof Error ? err.message : undefined,
      }),
  });

  const onArchive = (p: Project) => archiveMutation.mutate(p.id);
  const onRestore = (p: Project) => restoreMutation.mutate(p.id);

  return (
    <div className="w-full space-y-6 p-4 md:px-10 md:py-8">
      {/* Editorial page header — dashboard-northstar archetype */}
      <header
        className="flex flex-col items-start justify-between gap-4 sm:flex-row sm:items-end"
        style={{
          animationName: "rise",
          animationDuration: "0.6s",
          animationTimingFunction: "cubic-bezier(0.2, 0.7, 0.2, 1)",
          animationFillMode: "both",
        }}
      >
        <div>
          <PageTitle>Projects</PageTitle>

          <div className="flex flex-wrap items-center gap-2">
            {scope !== null && (
              <ScopeChip
                kind={isOrgWide ? "organization" : level}
                name={scopeName}
                size="sm"
              />
            )}
            {/* The count stays; the sentence explaining what a project card
                looks like does not. The count is of AUTHORIZED projects — the
                server filtered before paging (app/api/projects/route.ts) — so
                it is honest for the viewer rather than an org-wide figure they
                can only partly open. */}
            {pagination && (
              <span className="text-muted-foreground font-mono text-[11.5px]">
                {pagination.total} of {groups.length}{" "}
                {groups.length === 1
                  ? BUSINESS_UNIT_LABEL.toLowerCase()
                  : BUSINESS_UNIT_LABEL_PLURAL.toLowerCase()}
                {managedProjectIds.length > 0 && !isOrgWide
                  ? ` · you administer ${managedProjectIds.length}`
                  : ""}
              </span>
            )}
          </div>
        </div>

        <RequireRole
          capability="project:create"
          fallback={
            <Button disabled title="Your role cannot create projects" className="shrink-0">
              <Plus className="size-4" aria-hidden />
              New project
            </Button>
          }
        >
          <Button
            onClick={() => setDialogOpen(true)}
            className="shrink-0 bg-gradient-to-br from-brand-gradient-from to-brand-gradient-to font-semibold text-white shadow-[0_6px_18px_-6px_oklch(0.6_0.2_35_/_0.65)] transition-shadow hover:shadow-[0_10px_26px_-8px_oklch(0.6_0.2_35_/_0.8)]"
          >
            <Plus className="size-4" aria-hidden />
            New project
          </Button>
        </RequireRole>
      </header>

      {/* Metric strip — real counts from listProjects data */}
      {data && (
        <div
          className="grid grid-cols-2 gap-3 sm:grid-cols-4"
          aria-label="Project metrics"
        >
          <MetricTile
            label="Total projects"
            value={pagination?.total ?? items.length}
            foot={archivedCount > 0 ? `${archivedCount} archived` : undefined}
            delay={0.06}
          />
          <MetricTile
            label="Active"
            value={activeCount}
            foot={runningCount > 0 ? `${runningCount} running` : "none running"}
            delay={0.12}
          />
          <MetricTile
            label="Running"
            value={runningCount}
            foot={runningCount > 0 ? "in progress" : "all idle"}
            accent={runningCount > 0}
            delay={0.18}
          />
          <MetricTile
            label="Awaiting approval"
            value={awaitingCount}
            foot={awaitingCount > 0 ? "needs review" : "none pending"}
            accent={awaitingCount > 0}
            delay={0.24}
          />
        </div>
      )}

      {/* Which projects are costing what, this month. Sits above the toolbar
          because it describes the whole list rather than the filtered view —
          it does not follow the search or template filters. */}
      <SpendBreakdownCard groupBy="project" />

      <ProjectsToolbar
        value={toolbarState}
        onChange={(patch) => {
          // Any toolbar change resets to page 1
          updateParams({ ...patch, page: 1 });
        }}
      />

      {isLoading && !data && <LoadingState variant="list" rows={4} />}

      {isError && (
        <ApiErrorState
          title="Couldn't load projects"
          error={
            error && "code" in error && "message" in error
              ? (error as { code: string; message: string; requestId?: string })
              : undefined
          }
          description={
            !(error && "code" in error)
              ? (error instanceof Error ? error.message : "Unknown error.")
              : undefined
          }
          onRetry={() => refetch()}
        />
      )}

      {data &&
        items.length === 0 &&
        // Three distinct empties, three distinct messages: no assignment at all,
        // an active filter that matched nothing, or a genuinely empty scope.
        // They previously all rendered as the same "no projects" card, which
        // reads as a broken page to the one person who most needs clarity.
        (unbound && !toolbarState.search && toolbarState.template === "all" ? (
          <NoScopeAccess resource="projects" />
        ) : (
          <EmptyProjects
            isFiltered={!!toolbarState.search || toolbarState.template !== "all"}
            canCreate={canCreate}
            onCreateProject={() => setDialogOpen(true)}
          />
        ))}

      {data && items.length > 0 && (
        <>
          <div className="flex flex-col gap-8">
            {groups.map((group) => (
              <section key={group.label} aria-label={group.label}>
                <div className="mb-3 flex items-center gap-2.5">
                  <h2 className="font-display text-[15px] font-semibold tracking-tight">
                    {group.label}
                  </h2>
                  <span className="text-muted-foreground font-mono text-[11px]">
                    {group.items.length} project{group.items.length === 1 ? "" : "s"}
                  </span>
                  <span className="h-px flex-1 bg-line-soft" aria-hidden />
                </div>

                {toolbarState.view === "grid" ? (
                  <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
                    {group.items.map((p, i) => (
                      <ProjectCard
                        key={p.id}
                        project={p}
                        onArchive={onArchive}
                        onRestore={onRestore}
                        style={{
                          animationDelay: `${0.1 + i * 0.05}s`,
                        } as React.CSSProperties}
                      />
                    ))}
                  </div>
                ) : (
                  <ul className="flex flex-col gap-2">
                    {group.items.map((p) => (
                      <ProjectCard
                        key={p.id}
                        project={p}
                        variant="row"
                        onArchive={onArchive}
                        onRestore={onRestore}
                      />
                    ))}
                  </ul>
                )}
              </section>
            ))}
          </div>

          {pagination && pagination.total > pagination.pageSize && (
            <nav
              aria-label="Pagination"
              className="text-muted-foreground flex items-center justify-between border-t border-line-soft pt-4 text-sm"
            >
              <span className="font-mono text-xs">
                Showing {(pagination.page - 1) * pagination.pageSize + 1}–
                {Math.min(pagination.page * pagination.pageSize, pagination.total)} of{" "}
                {pagination.total}
              </span>
              <div className="flex gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  disabled={pagination.page <= 1}
                  onClick={() => updateParams({ page: pagination.page - 1 })}
                  className="border-line-soft"
                >
                  Previous
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={pagination.page >= totalPages}
                  onClick={() => updateParams({ page: pagination.page + 1 })}
                  className="border-line-soft"
                >
                  Next
                </Button>
              </div>
            </nav>
          )}
        </>
      )}

      <CreateProjectDialog open={dialogOpen} onOpenChange={closeDialog} />
    </div>
  );
}

// ── Metric strip tile — binds real counts from listProjects ──────────────────

interface MetricTileProps {
  label: string;
  value: number;
  foot?: string;
  accent?: boolean;
  delay?: number;
}

function MetricTile({ label, value, foot, accent, delay = 0 }: MetricTileProps) {
  return (
    <div
      className={cn(
        "relative overflow-hidden rounded-xl border border-line-soft bg-panel-elevated px-[18px] py-[18px]",
        "shadow-[0_1px_0_oklch(1_0_0_/_0.04)_inset,0_8px_20px_-8px_oklch(0_0_0_/_0.4)]",
      )}
      style={{
        animationName: "rise",
        animationDuration: "0.6s",
        animationTimingFunction: "cubic-bezier(0.2, 0.7, 0.2, 1)",
        animationFillMode: "both",
        animationDelay: `${delay}s`,
      }}
    >
      <p className="font-mono text-[11.5px] font-semibold uppercase tracking-[0.08em] text-muted-foreground">
        {label}
      </p>
      <p className="font-display mt-2 text-[30px] font-bold leading-none tracking-[-0.02em]">
        {value}
      </p>
      {foot && (
        <p
          className={cn(
            "mt-2 font-mono text-[11.5px]",
            accent ? "text-success" : "text-muted-foreground",
          )}
        >
          {accent && value > 0 && <span className="mr-1 text-success">●</span>}
          {foot}
        </p>
      )}
    </div>
  );
}
