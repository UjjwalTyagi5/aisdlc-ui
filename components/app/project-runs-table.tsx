"use client";

import * as React from "react";
import Link from "next/link";
import { formatDistanceToNow, formatDuration, intervalToDuration } from "date-fns";

import { cn } from "@/lib/utils";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { CostBadge } from "@/components/ui/cost-badge";
import { StatusBadge } from "@/components/ui/status-badge";
import { EmptyState } from "@/components/ui/empty-state";
import { LoadingState } from "@/components/ui/loading-state";
import { AGENT_LABEL } from "@/lib/agents";
import type { Run } from "@/lib/schemas";

export interface ProjectRunsTableProps {
  runs: readonly Run[] | null;
  isLoading?: boolean;
  /** Hide the project column when rendered inside a project overview. */
  showProject?: boolean;
  /** projectId → display name; the project cell falls back to the raw id. */
  projectNames?: Record<string, string>;
  className?: string;
}

function formatDurationMs(ms: number): string {
  if (ms < 60_000) return `${Math.round(ms / 1000)}s`;
  const duration = intervalToDuration({ start: 0, end: ms });
  return formatDuration(duration, {
    format: duration.hours ? ["hours", "minutes"] : ["minutes", "seconds"],
  });
}

export function ProjectRunsTable({
  runs,
  isLoading,
  showProject = false,
  projectNames,
  className,
}: ProjectRunsTableProps) {
  if (isLoading || runs === null) {
    return <LoadingState variant="table" rows={5} className={className} />;
  }
  if (runs.length === 0) {
    return (
      <EmptyState
        title="No runs yet"
        description="Once an agent runs, you'll see it here."
        variant="card"
        className={className}
      />
    );
  }
  return (
    <div className={cn("overflow-hidden rounded-xl border border-line-soft", className)}>
      <Table>
        <TableHeader>
          <TableRow className="border-b border-line-soft bg-surface-1 hover:bg-surface-1">
            <TableHead className="font-mono text-[11px] uppercase tracking-wider text-muted-foreground">
              Run
            </TableHead>
            <TableHead className="font-mono text-[11px] uppercase tracking-wider text-muted-foreground">
              Agent
            </TableHead>
            {showProject && (
              <TableHead className="font-mono text-[11px] uppercase tracking-wider text-muted-foreground">
                Project
              </TableHead>
            )}
            <TableHead className="font-mono text-[11px] uppercase tracking-wider text-muted-foreground">
              Status
            </TableHead>
            <TableHead className="text-right font-mono text-[11px] uppercase tracking-wider text-muted-foreground">
              Duration
            </TableHead>
            <TableHead className="text-right font-mono text-[11px] uppercase tracking-wider text-muted-foreground">
              Cost
            </TableHead>
            <TableHead className="text-right font-mono text-[11px] uppercase tracking-wider text-muted-foreground">
              Started
            </TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {runs.map((r) => (
            <TableRow
              key={r.id}
              className="border-b border-line-soft transition-colors hover:bg-surface-1/60"
            >
              <TableCell className="py-3">
                <Link
                  href={`/projects/${r.projectId}/copilot?run=${r.id}`}
                  className="block truncate text-[13px] font-medium text-brand-bright underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1"
                >
                  {r.title}
                </Link>
                <div className="text-muted-foreground mt-0.5 truncate font-mono text-[11px]">
                  {r.id}
                </div>
              </TableCell>
              <TableCell className="text-[13px] text-foreground/80">{AGENT_LABEL[r.agent]}</TableCell>
              {showProject && (
                <TableCell>
                  <Link
                    href={`/projects/${r.projectId}`}
                    className="text-[13px] text-foreground/80 underline-offset-2 hover:text-foreground hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1"
                  >
                    {projectNames?.[r.projectId] ?? r.projectId}
                  </Link>
                </TableCell>
              )}
              <TableCell>
                <StatusBadge status={r.status} />
              </TableCell>
              <TableCell className="text-right font-mono text-[12px] text-muted-foreground">
                {r.durationMs !== null ? formatDurationMs(r.durationMs) : "—"}
              </TableCell>
              <TableCell className="text-right">
                <CostBadge
                  usd={r.cost.usd}
                  tokens={r.cost.inputTokens + r.cost.outputTokens}
                />
              </TableCell>
              <TableCell className="text-right font-mono text-[11px] text-muted-foreground">
                {formatDistanceToNow(new Date(r.startedAt), { addSuffix: true })}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
