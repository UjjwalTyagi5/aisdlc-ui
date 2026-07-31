"use client";

import * as React from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Activity, Download } from "lucide-react";

import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { EmptyState } from "@/components/ui/empty-state";
import { Separator } from "@/components/ui/separator";
import { ApiErrorState } from "@/components/feedback/api-error-state";

import { ProjectRunsTable } from "@/components/app/project-runs-table";
import { RunDetailDrawer } from "@/components/app/run-detail-drawer";

import { listProjects } from "@/lib/api/projects";
import { listRuns } from "@/lib/api/runs";
import { qk } from "@/lib/api/query-keys";
import { downloadCsv } from "@/lib/export";
import type { ProjectId, Run } from "@/lib/schemas";

const PAGE_SIZE = 25;

export default function RunsPage() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const queryClient = useQueryClient();

  const projectFilter = (searchParams.get("projectId") ?? "all") as "all" | ProjectId;
  const statusFilter = searchParams.get("status") ?? "all";
  const agentFilter = searchParams.get("agent") ?? "all";
  const search = searchParams.get("q") ?? "";
  const page = Number(searchParams.get("page") ?? "1");

  const updateParams = React.useCallback(
    (patch: Record<string, string | number | undefined>) => {
      const next = new URLSearchParams(searchParams);
      for (const [k, v] of Object.entries(patch)) {
        if (v === undefined || v === "" || v === "all" || String(v) === "1") {
          next.delete(k);
        } else {
          next.set(k, String(v));
        }
      }
      router.replace(`/runs?${next.toString()}`);
    },
    [router, searchParams],
  );

  const projectsQ = useQuery({
    queryKey: qk.projects.list({ kind: "picker" }),
    queryFn: () => listProjects({ pageSize: 100 }),
  });

  const runsQ = useQuery({
    queryKey: qk.runs.list({
      projectId: projectFilter,
      status: statusFilter,
      page,
    }),
    queryFn: () =>
      listRuns({
        projectId: projectFilter === "all" ? undefined : (projectFilter as ProjectId),
        status: statusFilter === "all" ? undefined : statusFilter,
        page,
        pageSize: PAGE_SIZE,
      }),
    placeholderData: (prev) => prev,
  });

  const projectNameById = React.useMemo(() => {
    const map: Record<string, string> = {};
    for (const p of projectsQ.data?.items ?? []) map[p.id] = p.name;
    return map;
  }, [projectsQ.data]);

  const items = React.useMemo(() => {
    const base = runsQ.data?.items ?? [];
    let next = base;
    if (agentFilter !== "all") next = next.filter((r) => r.agent === agentFilter);
    if (search.trim()) {
      const q = search.toLowerCase();
      next = next.filter(
        (r) =>
          r.title.toLowerCase().includes(q) ||
          r.id.toLowerCase().includes(q) ||
          r.projectId.toLowerCase().includes(q) ||
          (projectNameById[r.projectId] ?? "").toLowerCase().includes(q),
      );
    }
    return next;
  }, [runsQ.data, agentFilter, search, projectNameById]);

  const [drawerRun, setDrawerRun] = React.useState<Run | null>(null);
  const [replayBusy, setReplayBusy] = React.useState(false);

  const replay = async (run: Run) => {
    setReplayBusy(true);
    toast.message(`Queued replay of ${run.id}`, {
      description: "Wires to the orchestrator in Chunk 16.",
    });
    // Simulate a queued->running flip by invalidating runs after a tick
    await new Promise((r) => setTimeout(r, 800));
    void queryClient.invalidateQueries({ queryKey: qk.runs.all() });
    setReplayBusy(false);
  };

  const exportCsv = () => {
    const rows = items.map((r) => ({
      id: r.id,
      project: projectNameById[r.projectId] ?? r.projectId,
      agent: r.agent,
      phase: r.phase,
      status: r.status,
      trigger: r.trigger,
      started_at: r.startedAt,
      completed_at: r.completedAt ?? "",
      duration_ms: r.durationMs ?? "",
      cost_usd: r.cost.usd,
      input_tokens: r.cost.inputTokens,
      output_tokens: r.cost.outputTokens,
      title: r.title,
    }));
    downloadCsv(`runs-${new Date().toISOString().slice(0, 10)}.csv`, rows, [
      { key: "id", header: "run_id" },
      { key: "project", header: "project" },
      { key: "agent", header: "agent" },
      { key: "phase", header: "phase" },
      { key: "status", header: "status" },
      { key: "trigger", header: "trigger" },
      { key: "started_at", header: "started_at" },
      { key: "completed_at", header: "completed_at" },
      { key: "duration_ms", header: "duration_ms" },
      { key: "cost_usd", header: "cost_usd" },
      { key: "input_tokens", header: "input_tokens" },
      { key: "output_tokens", header: "output_tokens" },
      { key: "title", header: "title" },
    ]);
    toast.success(`Exported ${items.length} runs`);
  };

  const pagination = runsQ.data?.pagination;
  const totalPages = pagination
    ? Math.max(1, Math.ceil(pagination.total / pagination.pageSize))
    : 1;

  return (
    <div className="w-full space-y-5 p-4 md:px-10 md:py-8">
      <header className="flex flex-col items-start justify-between gap-3 sm:flex-row sm:items-center">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Runs</h1>
          <p className="text-muted-foreground text-sm">
            {pagination
              ? `${pagination.total} total · page ${pagination.page} of ${totalPages}`
              : "Loading…"}
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={exportCsv} disabled={items.length === 0}>
          <Download className="size-4" aria-hidden />
          Export CSV
        </Button>
      </header>

      {/* Filters */}
      <div className="flex flex-wrap items-center gap-2">
        <Input
          value={search}
          onChange={(e) => updateParams({ q: e.target.value || undefined, page: undefined })}
          placeholder="Search by title, id, project…"
          className="h-9 w-64"
          aria-label="Search runs"
        />
        <Select
          value={projectFilter}
          onValueChange={(v) => updateParams({ projectId: v, page: undefined })}
        >
          <SelectTrigger className="h-9 w-48" aria-label="Filter by project">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All projects</SelectItem>
            {projectsQ.data?.items.map((p) => (
              <SelectItem key={p.id} value={p.id}>
                {p.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select
          value={agentFilter}
          onValueChange={(v) => updateParams({ agent: v, page: undefined })}
        >
          <SelectTrigger className="h-9 w-40" aria-label="Filter by agent">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All agents</SelectItem>
            {[
              "orchestrator",
              "requirements",
              "design",
              "development",
              "review",
              "testing",
              "deployment",
            ].map((a) => (
              <SelectItem key={a} value={a} className="capitalize">
                {a}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select
          value={statusFilter}
          onValueChange={(v) => updateParams({ status: v, page: undefined })}
        >
          <SelectTrigger className="h-9 w-40" aria-label="Filter by status">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">Any status</SelectItem>
            {[
              "draft",
              "queued",
              "running",
              "awaiting_approval",
              "approved",
              "rejected",
              "failed",
              "merged",
            ].map((s) => (
              <SelectItem key={s} value={s} className="capitalize">
                {s.replace(/_/g, " ")}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {/* Body */}
      {runsQ.isError ? (
        <ApiErrorState
          title="Couldn't load runs"
          error={
            runsQ.error && "code" in runsQ.error && "message" in runsQ.error
              ? (runsQ.error as { code: string; message: string; requestId?: string })
              : undefined
          }
          description={
            !(runsQ.error && "code" in runsQ.error)
              ? (runsQ.error instanceof Error ? runsQ.error.message : "Unknown error.")
              : undefined
          }
          onRetry={() => runsQ.refetch()}
        />
      ) : items.length === 0 && !runsQ.isLoading ? (
        <EmptyState
          icon={Activity}
          title="No runs match"
          description="Clear a filter or start an agent run from a project."
        />
      ) : (
        <div
          className={cn(
            "transition-opacity",
            runsQ.isFetching && !runsQ.isLoading && "opacity-70",
          )}
        >
          <ClickableRunsTable
            runs={runsQ.isLoading ? null : items}
            projectNames={projectNameById}
            onRowClick={(r) =>
              router.push(`/projects/${r.projectId}/copilot?run=${r.id}`)
            }
          />
        </div>
      )}

      {pagination && pagination.total > pagination.pageSize && (
        <nav
          aria-label="Pagination"
          className="text-muted-foreground flex items-center justify-between border-t pt-3 text-sm"
        >
          <span>
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
            >
              Previous
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={pagination.page >= totalPages}
              onClick={() => updateParams({ page: pagination.page + 1 })}
            >
              Next
            </Button>
          </div>
        </nav>
      )}

      <RunDetailDrawer
        run={drawerRun}
        open={drawerRun !== null}
        onOpenChange={(v) => !v && setDrawerRun(null)}
        onReplay={replay}
        replayBusy={replayBusy}
      />

      <Separator className="opacity-0" />
    </div>
  );
}

// ───────── clickable variant ─────────

function ClickableRunsTable({
  runs,
  onRowClick,
  projectNames,
}: {
  runs: readonly Run[] | null;
  onRowClick: (r: Run) => void;
  projectNames?: Record<string, string>;
}) {
  // We wrap `ProjectRunsTable` in a click-capture overlay so rows open the drawer.
  // Fine for MVP; proper row-click support can be added to the table primitive later.
  const ref = React.useRef<HTMLDivElement>(null);
  React.useEffect(() => {
    const el = ref.current;
    if (!el || !runs) return;
    const onClick = (e: MouseEvent) => {
      const target = e.target as HTMLElement | null;
      if (!target) return;
      if (target.closest("a, button")) return; // let inner links work
      const row = target.closest("tr") as HTMLTableRowElement | null;
      if (!row) return;
      const index = Array.from(row.parentElement?.children ?? []).indexOf(row);
      if (index < 0) return;
      // Body rows only — header has a separate <tr> in <thead>, same parent type.
      // The parent of the body rows is the <tbody>, so index here is correct.
      const run = runs[index];
      if (run) onRowClick(run);
    };
    el.addEventListener("click", onClick);
    return () => el.removeEventListener("click", onClick);
  }, [runs, onRowClick]);

  return (
    <div ref={ref} className="cursor-pointer [&_tbody_tr:hover]:bg-accent/60">
      <ProjectRunsTable runs={runs} showProject projectNames={projectNames} />
    </div>
  );
}
