"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { Waypoints } from "lucide-react";

import { ApiErrorState } from "@/components/feedback/api-error-state";
import { EmptyState } from "@/components/ui/empty-state";
import { LoadingState } from "@/components/ui/loading-state";
import { StatusBadge } from "@/components/ui/status-badge";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { formatUsd } from "@/components/app/cost-dashboard";
import { listTraces, type TraceFilters } from "@/lib/api/traces";
import { qk } from "@/lib/api/query-keys";
import { AgentType, type TraceListItem, type TraceScore } from "@/lib/schemas";

const ALL = "all";
const AGENT_OPTIONS = AgentType.options;
const STATUS_OPTIONS = ["approved", "failed", "running", "awaiting_approval"] as const;

function avgScore(scores: TraceScore[]): string {
  if (scores.length === 0) return "—";
  const avg = scores.reduce((a, s) => a + s.value, 0) / scores.length;
  return avg.toFixed(2);
}

function fmtLatency(ms: number): string {
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`;
}

export interface TracesExplorerProps {
  agent: string;
  project: string;
  status: string;
  onAgentChange: (v: string) => void;
  onProjectChange: (v: string) => void;
  onStatusChange: (v: string) => void;
}

export function TracesExplorer({
  agent,
  project,
  status,
  onAgentChange,
  onProjectChange,
  onStatusChange,
}: TracesExplorerProps) {
  const router = useRouter();

  const filters: TraceFilters = {
    agent: agent === ALL ? undefined : agent,
    project: project === ALL ? undefined : project,
    status: status === ALL ? undefined : status,
  };

  const tracesQ = useQuery({
    queryKey: qk.traces.list(filters),
    queryFn: () => listTraces(filters),
  });

  // Project options derived from the returned rows (dedupe by projectId).
  const projectOptions = React.useMemo(() => {
    const map = new Map<string, string>();
    for (const t of tracesQ.data ?? []) map.set(t.projectId, t.projectName);
    return Array.from(map.entries());
  }, [tracesQ.data]);

  return (
    <div className="space-y-4">
      {/* Filters */}
      <div className="flex flex-wrap items-center gap-3">
        <FilterSelect label="Agent" value={agent} onChange={onAgentChange} options={AGENT_OPTIONS.map((a) => [a, a])} />
        <FilterSelect label="Project" value={project} onChange={onProjectChange} options={projectOptions} />
        <FilterSelect
          label="Status"
          value={status}
          onChange={onStatusChange}
          options={STATUS_OPTIONS.map((s) => [s, s.replace(/_/g, " ")])}
        />
      </div>

      {tracesQ.isError ? (
        <ApiErrorState
          title="Couldn't load traces"
          error={
            tracesQ.error && "code" in tracesQ.error && "message" in tracesQ.error
              ? (tracesQ.error as { code: string; message: string; requestId?: string })
              : undefined
          }
          description={
            !(tracesQ.error && "code" in tracesQ.error)
              ? tracesQ.error instanceof Error
                ? tracesQ.error.message
                : "Unknown error."
              : undefined
          }
          onRetry={() => tracesQ.refetch()}
        />
      ) : tracesQ.isLoading ? (
        <LoadingState variant="table" rows={8} />
      ) : (tracesQ.data ?? []).length === 0 ? (
        <EmptyState
          icon={Waypoints}
          title="No traces match"
          description="No agent traces match the selected filters in this window."
        />
      ) : (
        <div className="border-line-soft bg-panel-elevated overflow-hidden rounded-xl border shadow-[0_1px_0_oklch(1_0_0_/_0.04)_inset,0_8px_20px_-8px_oklch(0_0_0_/_0.35)]">
          <div className="border-line-soft flex items-center gap-2 border-b px-5 py-3.5">
            <span className="font-display text-[13.5px] font-bold tracking-[-0.01em]">Traces</span>
            <span className="text-muted-foreground font-mono text-[10.5px]">
              {tracesQ.data!.length} rows
            </span>
          </div>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Trace</TableHead>
                <TableHead>Agent</TableHead>
                <TableHead>Project</TableHead>
                <TableHead>Status</TableHead>
                <TableHead className="text-right">Latency</TableHead>
                <TableHead className="text-right">Cost</TableHead>
                <TableHead className="text-right">Spans</TableHead>
                <TableHead className="text-right">Score</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {tracesQ.data!.map((t: TraceListItem) => (
                <TableRow
                  key={t.id}
                  className="cursor-pointer"
                  onClick={() => router.push(`/traces/${t.id}`)}
                >
                  <TableCell>
                    <div className="flex flex-col">
                      <span className="font-medium">{t.name}</span>
                      <span className="text-muted-foreground font-mono text-[11px]">{t.id}</span>
                    </div>
                  </TableCell>
                  <TableCell className="font-mono text-[12px]">{t.agentType}</TableCell>
                  <TableCell className="text-muted-foreground text-[12px]">{t.projectName}</TableCell>
                  <TableCell>
                    <StatusBadge status={t.status} />
                  </TableCell>
                  <TableCell className="text-right font-mono text-[12px] tabular-nums">
                    {fmtLatency(t.latencyMs)}
                  </TableCell>
                  <TableCell className="text-right font-mono text-[12px] font-semibold">
                    {formatUsd(t.cost.usd)}
                  </TableCell>
                  <TableCell className="text-right font-mono text-[12px]">{t.spanCount}</TableCell>
                  <TableCell className="text-right font-mono text-[12px]">
                    {avgScore(t.scores)}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
    </div>
  );
}

function FilterSelect({
  label,
  value,
  onChange,
  options,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  options: readonly (readonly [string, string])[];
}) {
  return (
    <div className="flex items-center gap-2">
      <span className="text-muted-foreground font-mono text-[10.5px] uppercase tracking-[0.12em]">
        {label}
      </span>
      <Select value={value} onValueChange={onChange}>
        <SelectTrigger className="border-line-soft h-9 w-44" aria-label={`Filter by ${label}`}>
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="all">All</SelectItem>
          {options.map(([val, lbl]) => (
            <SelectItem key={val} value={val} className="capitalize">
              {lbl}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}
