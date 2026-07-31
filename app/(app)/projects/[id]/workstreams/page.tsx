"use client";

import * as React from "react";
import { useParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { Activity, Play } from "lucide-react";

import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { LoadingState } from "@/components/ui/loading-state";
import { ProjectRunsTable } from "@/components/app/project-runs-table";
import { RunTriggerDialog } from "@/components/runs/run-trigger-dialog";
import { RequireRole } from "@/components/auth/require-role";
import { listRuns } from "@/lib/api/runs";
import { qk } from "@/lib/api/query-keys";
import { WORKSTREAM_LABEL_PLURAL } from "@/lib/scope";
import type { ProjectId, Status } from "@/lib/schemas";

/**
 * Workstreams — PRD §32.1 ("Inside a Project") and §34.11.
 *
 * "A workstream is one conversation on a project for one piece of work; it
 * keeps the latest artifact per stage so later agents build on earlier ones."
 *
 * Nothing here auto-advances: a workstream pauses at a gate, a budget cap, a
 * suspension or a clarification, and the user drives every hand-off.
 *
 * The code calls these `runs`; the PRD calls them workstreams. The label is
 * the PRD's, the route and API stay as they are (see lib/scope.ts).
 */

type StatusFilter = "all" | "active" | "paused" | "done";

const FILTERS: { id: StatusFilter; label: string; match: (s: Status) => boolean }[] = [
  { id: "all", label: "All", match: () => true },
  {
    id: "active",
    label: "In flight",
    match: (s) => s === "running" || s === "queued" || s === "draft",
  },
  {
    id: "paused",
    label: "Paused at a gate",
    match: (s) => s === "awaiting_approval" || s === "awaiting_clarification" || s === "paused",
  },
  {
    id: "done",
    label: "Closed",
    match: (s) =>
      s === "approved" || s === "merged" || s === "rejected" || s === "failed" || s === "cancelled",
  },
];

export default function ProjectWorkstreamsPage() {
  const params = useParams<{ id: string }>();
  const id = params.id as ProjectId;
  const [filter, setFilter] = React.useState<StatusFilter>("all");
  const [runOpen, setRunOpen] = React.useState(false);

  const runsQ = useQuery({
    queryKey: qk.runs.forProject(id),
    queryFn: () => listRuns({ projectId: id, pageSize: 100 }),
  });

  const runs = React.useMemo(() => runsQ.data?.items ?? [], [runsQ.data]);
  const active = FILTERS.find((f) => f.id === filter)!;
  const visible = runs.filter((r) => active.match(r.status));

  const counts = React.useMemo(
    () =>
      Object.fromEntries(
        FILTERS.map((f) => [f.id, runs.filter((r) => f.match(r.status)).length]),
      ) as Record<StatusFilter, number>,
    [runs],
  );

  return (
    <div className="w-full space-y-5 p-4 md:px-10 md:py-8">
      <RunTriggerDialog projectId={id} open={runOpen} onOpenChange={setRunOpen} />

      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="font-display text-lg font-semibold tracking-tight">
            {WORKSTREAM_LABEL_PLURAL}
          </h2>
          <p className="text-muted-foreground mt-1 max-w-2xl text-[13px]">
            One conversation per piece of work. Each keeps the latest artifact
            per stage, so later agents build on earlier ones. Nothing advances
            on its own.
          </p>
        </div>

        <RequireRole capability="run:trigger">
          <Button onClick={() => setRunOpen(true)} className="gap-2">
            <Play className="size-4" aria-hidden />
            Start a workstream
          </Button>
        </RequireRole>
      </div>

      {/* Status filter */}
      <div className="border-line-soft inline-flex flex-wrap rounded-lg border p-0.5">
        {FILTERS.map((f) => (
          <button
            key={f.id}
            type="button"
            onClick={() => setFilter(f.id)}
            aria-pressed={filter === f.id}
            className={cn(
              "rounded-md px-3 py-1.5 text-[12.5px] font-medium transition-colors",
              filter === f.id
                ? "bg-surface-2 text-foreground"
                : "text-muted-foreground hover:text-foreground",
            )}
          >
            {f.label}
            <span className="text-muted-foreground/70 ml-1.5 font-mono text-[11px]">
              {counts[f.id] ?? 0}
            </span>
          </button>
        ))}
      </div>

      {runsQ.isError ? (
        <ErrorState
          title="Couldn't load workstreams"
          description={
            runsQ.error instanceof Error ? runsQ.error.message : "Unknown error."
          }
          onRetry={() => runsQ.refetch()}
        />
      ) : runsQ.isLoading ? (
        <LoadingState variant="list" rows={5} />
      ) : visible.length === 0 ? (
        <EmptyState
          icon={Activity}
          title={
            filter === "all"
              ? "No workstreams yet"
              : `Nothing ${active.label.toLowerCase()}`
          }
          description={
            filter === "all"
              ? "Start a workstream to open a conversation with an agent on this project."
              : "Try a different filter to see the rest of this project's work."
          }
        />
      ) : (
        <ProjectRunsTable runs={visible} />
      )}
    </div>
  );
}
