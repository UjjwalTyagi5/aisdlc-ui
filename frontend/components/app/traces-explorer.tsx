"use client";

import * as React from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { Waypoints } from "lucide-react";

import { ApiErrorState } from "@/components/feedback/api-error-state";
import { EmptyState } from "@/components/ui/empty-state";
import { LoadingState } from "@/components/ui/loading-state";
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
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { SortableHead, type SortDir } from "@/components/ui/sortable-header";
import { formatUsd } from "@/components/app/cost-dashboard";
import { listTraces, type TraceFilters } from "@/lib/api/traces";
import { qk } from "@/lib/api/query-keys";
import { AgentType, type TraceListItem } from "@/lib/schemas";

const ALL = "all";

// SCORE IS NOT A COLUMN HERE. `_map_trace_list_item` in shared/routers/traces.py sets
// `scores=[]` unconditionally, so the list endpoint has never carried one — and asking
// Langfuse does not help: not one trace across all four bound projects has a score
// attached (checked 2026-09-15), because nothing on this platform writes evaluations
// yet. The column rendered "—" on every row for both reasons at once.
//
// The trace DETAIL endpoint does map real scores, so the data has a home the moment
// evaluations start being written; bring the column back then, and populate it from
// the `scores` key Langfuse already returns on the list rather than from `[]`.
const AGENT_OPTIONS = AgentType.options;

function fmtLatency(ms: number): string {
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`;
}

/**
 * How each column is ordered. Keyed off the UNDERLYING value, never the rendered
 * string: `fmtLatency` turns 900ms into "900ms" and 1,100ms into "1.1s", and sorting
 * those as text puts the slower trace first. Same trap with cost ("$0.0052") and score
 * ("—" for no scores).
 *
 */
type TraceSortKey = "name" | "agent" | "project" | "latency" | "cost" | "spans";

const TRACE_SORTERS: Record<TraceSortKey, (t: TraceListItem) => string | number> = {
  name: (t) => t.name,
  agent: (t) => t.agentType ?? "",
  project: (t) => t.projectName,
  latency: (t) => t.latencyMs,
  cost: (t) => t.cost.usd,
  spans: (t) => t.spanCount,
};

export interface TracesExplorerProps {
  agent: string;
  project: string;
  user: string;
  onAgentChange: (v: string) => void;
  onProjectChange: (v: string) => void;
  onUserChange: (v: string) => void;
}

export function TracesExplorer({
  agent,
  project,
  user,
  onAgentChange,
  onProjectChange,
  onUserChange,
}: TracesExplorerProps) {
  const router = useRouter();

  const filters: TraceFilters = {
    agent: agent === ALL ? undefined : agent,
    project: project === ALL ? undefined : project,
    user: user === ALL ? undefined : user,
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

  // Member options, same derivation. Rows predating the attribution fix carry no
  // userId, so this is empty until traced runs exist — and an empty select is worse
  // than none, hence the conditional render below.
  const userOptions = React.useMemo(() => {
    const ids = new Set<string>();
    for (const t of tracesQ.data ?? []) if (t.userId) ids.add(t.userId);
    return Array.from(ids).map((id) => [id, id] as [string, string]);
  }, [tracesQ.data]);

  /**
   * SORTED IN THE BROWSER, and here that is the honest place for it.
   *
   * `listTraces` returns a flat array — every row the filters matched, up to the
   * endpoint's limit — so this sorts the WHOLE result, not a page of it. The Audit
   * Trail deliberately does the opposite: it is cursor-paginated, so sorting there
   * happens server-side, because reordering one page and calling the trail sorted is
   * a lie the reader cannot see through.
   */
  const [sortKey, setSortKey] = React.useState<TraceSortKey>("latency");
  const [sortDir, setSortDir] = React.useState<SortDir>("desc");

  const sorted = React.useMemo(() => {
    const rows = [...(tracesQ.data ?? [])];
    const pick = TRACE_SORTERS[sortKey];
    rows.sort((a, b) => {
      const av = pick(a);
      const bv = pick(b);
      // localeCompare for strings so "Æ" and "z" land where a reader expects, numeric
      // subtraction for the rest; mixing the two silently sorts numbers as text and
      // puts 10 before 9.
      const cmp =
        typeof av === "string" || typeof bv === "string"
          ? String(av).localeCompare(String(bv), undefined, { numeric: true })
          : (av as number) - (bv as number);
      return sortDir === "asc" ? cmp : -cmp;
    });
    return rows;
  }, [tracesQ.data, sortKey, sortDir]);

  const sortProps = (key: TraceSortKey) => ({
    active: sortKey === key,
    dir: sortDir,
    onSort: (next: SortDir) => {
      setSortKey(key);
      setSortDir(next);
    },
  });

  return (
    <div className="space-y-4">
      {/* Filters */}
      <div className="flex flex-wrap items-center gap-3">
        <FilterSelect label="Agent" value={agent} onChange={onAgentChange} options={AGENT_OPTIONS.map((a) => [a, a])} />
        <FilterSelect label="Project" value={project} onChange={onProjectChange} options={projectOptions} />
        {userOptions.length > 0 && (
          <FilterSelect label="Member" value={user} onChange={onUserChange} options={userOptions} />
        )}
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
                <SortableHead label="Trace" {...sortProps("name")} />
                <SortableHead label="Agent" {...sortProps("agent")} />
                <SortableHead label="Project" {...sortProps("project")} />
                <SortableHead label="Latency" align="right" className="text-right" {...sortProps("latency")} />
                <SortableHead label="Cost" align="right" className="text-right" {...sortProps("cost")} />
                <SortableHead label="Spans" align="right" className="text-right" {...sortProps("spans")} />
              </TableRow>
            </TableHeader>
            <TableBody>
              {sorted.map((t: TraceListItem) => (
                // REACHABLE WITHOUT A MOUSE.
                //
                // This was a bare `onClick` on the row. A <tr> is not focusable and has
                // no implicit role, so the accessibility tree showed ZERO interactive
                // elements in this table — the trace detail was reachable only by
                // clicking, and not at all by keyboard or screen reader. The row keeps
                // its click target (the whole row is the affordance people expect), and
                // the trace name is now a real link, which also restores open-in-new-tab
                // and the status-bar URL preview.
                <TableRow
                  key={t.id}
                  className="cursor-pointer"
                  onClick={() => router.push(`/traces/${t.id}`)}
                >
                  <TableCell>
                    <div className="flex flex-col">
                      <Link
                        href={`/traces/${t.id}`}
                        className="font-medium hover:underline focus-visible:ring-ring rounded-sm focus-visible:ring-2 focus-visible:outline-none"
                        // The row's own handler would fire too and push the same route
                        // twice, which leaves a duplicate history entry and makes Back
                        // feel broken.
                        onClick={(e) => e.stopPropagation()}
                      >
                        {t.name}
                      </Link>
                      <span className="text-muted-foreground font-mono text-[11px]">{t.id}</span>
                    </div>
                  </TableCell>
                  <TableCell className="font-mono text-[12px]">{t.agentType}</TableCell>
                  <TableCell className="text-muted-foreground text-[12px]">{t.projectName}</TableCell>
                  <TableCell className="text-right font-mono text-[12px] tabular-nums">
                    {fmtLatency(t.latencyMs)}
                  </TableCell>
                  <TableCell className="text-right font-mono text-[12px] font-semibold">
                    {formatUsd(t.cost.usd)}
                  </TableCell>
                  <TableCell className="text-right font-mono text-[12px]">{t.spanCount}</TableCell>
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
