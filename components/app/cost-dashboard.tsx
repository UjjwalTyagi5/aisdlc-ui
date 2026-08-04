"use client";

import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { Coins } from "lucide-react";

import { ApiErrorState } from "@/components/feedback/api-error-state";
import { CostMeter } from "@/components/app/cost-meter";
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
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { getBudgets, getCostBreakdown, type CostBreakdown, type CostBreakdownRow } from "@/lib/api/cost";
import { qk } from "@/lib/api/query-keys";
import { PHASE_LABEL } from "@/lib/agents";
import { BUSINESS_UNIT_LABEL, BUSINESS_UNIT_LABEL_PLURAL } from "@/lib/scope";

export const WINDOW_OPTIONS = [7, 30, 90] as const;
export type WindowDays = (typeof WINDOW_OPTIONS)[number];

/** Budget-bar tone derived from utilization — mirrors CostMeter's pctOfCap tone logic. */
export type BudgetTone = "destructive" | "warning" | "success";

/**
 * Pure helper: derive the budget bar tone from a CostBreakdown envelope.
 * Returns null when budgetUsd is 0/absent (ED-5: no fake metric → bar hidden).
 */
export function budgetTone(data: Pick<CostBreakdown, "budgetUsd" | "utilization" | "breached80">): BudgetTone | null {
  if (!data.budgetUsd || data.budgetUsd <= 0) return null;
  if (data.breached80 || data.utilization >= 1) return "destructive";
  if (data.utilization >= 0.8) return "warning";
  return "success";
}

/** Pure helper: format USD using the same convention as CostMeter. */
export function formatUsd(n: number): string {
  if (n >= 1) return `$${n.toFixed(2)}`;
  if (n < 0.01) return `$${n.toFixed(4)}`;
  return `$${n.toFixed(3)}`;
}

/** Pure helper: format a token count compactly (k/M). */
export function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

/**
 * Pure helper: row key for React lists — one row per (agent, model) pair.
 *
 * The model alone stopped being unique once rows carry an agent: Development
 * and Testing both run on Sonnet, and two rows sharing a key make React reuse
 * one row's DOM for the other's data.
 */
export function rowKey(row: CostBreakdownRow): string {
  return `${row.agentType}::${row.model}`;
}

/** Pure helper: the agents present in a breakdown, in spend order. */
export function agentsInBreakdown(rows: CostBreakdownRow[]): string[] {
  const spend = new Map<string, number>();
  for (const r of rows) spend.set(r.agentType, (spend.get(r.agentType) ?? 0) + r.costUsd);
  return [...spend.entries()].sort((a, b) => b[1] - a[1]).map(([agent]) => agent);
}

export function CostDashboard() {
  const [windowDays, setWindowDays] = React.useState<WindowDays>(30);
  const [workspace, setWorkspace] = React.useState<string>("all");

  // Workspace options — reuse the budgets feed (org/workspace list), same query key as
  // the budget hub so it's fetched once and shared.
  const budgetsQ = useQuery({ queryKey: qk.cost.budgets(), queryFn: getBudgets });
  const workspaces = budgetsQ.data?.workspaces ?? [];
  const wsParam = workspace === "all" ? undefined : workspace;

  const costQ = useQuery({
    queryKey: qk.cost.breakdown(windowDays, wsParam),
    queryFn: () => getCostBreakdown(windowDays, wsParam),
  });

  return (
    <div className="space-y-6">
      {/* Scope: workspace + window selectors. Filtering by workspace separates the same
          model configured (BYOK) in different workspaces. */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <span className="font-mono text-[11px] uppercase tracking-[0.14em] text-muted-foreground">
          Scope
        </span>
        <div className="flex items-center gap-2">
          <Select value={workspace} onValueChange={setWorkspace}>
            <SelectTrigger
              className="h-9 w-52 border-line-soft"
              aria-label={`Filter by ${BUSINESS_UNIT_LABEL.toLowerCase()}`}
            >
              <SelectValue placeholder={`All ${BUSINESS_UNIT_LABEL_PLURAL.toLowerCase()}`} />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All {BUSINESS_UNIT_LABEL_PLURAL.toLowerCase()}</SelectItem>
              {workspaces.map((w) => (
                <SelectItem key={w.id} value={w.id}>
                  {w.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Select
            value={String(windowDays)}
            onValueChange={(v) => setWindowDays(Number(v) as WindowDays)}
          >
            <SelectTrigger className="h-9 w-36 border-line-soft" aria-label="Select spend window">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {WINDOW_OPTIONS.map((d) => (
                <SelectItem key={d} value={String(d)}>
                  Last {d} days
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>

      {costQ.isError ? (
        <ApiErrorState
          title="Couldn't load cost data"
          error={
            costQ.error && "code" in costQ.error && "message" in costQ.error
              ? (costQ.error as { code: string; message: string; requestId?: string })
              : undefined
          }
          description={
            !(costQ.error && "code" in costQ.error)
              ? costQ.error instanceof Error
                ? costQ.error.message
                : "Unknown error."
              : undefined
          }
          onRetry={() => costQ.refetch()}
        />
      ) : costQ.isLoading ? (
        <LoadingState variant="table" rows={6} />
      ) : (
        costQ.data && <CostDashboardBody data={costQ.data} />
      )}
    </div>
  );
}

function CostDashboardBody({ data }: { data: CostBreakdown }) {
  const tone = budgetTone(data);

  /**
   * The agent filter narrows the BREAKDOWN only — never the meter above it.
   *
   * Deliberate, and the opposite of the Business Unit filter, which does refetch
   * and move the headline. A budget belongs to a scope, not to an agent: there
   * is no "Development budget" to be 40% through, so recomputing utilization
   * against a filtered slice would draw a progress bar against a denominator
   * that does not exist. The meter stays "what your scope spent and may spend";
   * the table answers "where did it go", and the subtotal states how much of the
   * total the current slice accounts for.
   *
   * Client-side for the same reason: nothing it changes needs the server, so a
   * round-trip would only add latency to a dropdown.
   */
  const [agent, setAgent] = React.useState<string>("all");
  const agents = React.useMemo(() => agentsInBreakdown(data.rows), [data.rows]);

  // A filter left pinned to an agent that vanished — after switching unit or
  // window — would show an empty table with nothing explaining why.
  React.useEffect(() => {
    if (agent !== "all" && !agents.includes(agent)) setAgent("all");
  }, [agents, agent]);

  const rows = agent === "all" ? data.rows : data.rows.filter((r) => r.agentType === agent);
  const filteredUsd = rows.reduce((a, r) => a + r.costUsd, 0);

  return (
    <div className="space-y-6">
      {/* Total spend tile — reuses CostMeter for the headline + budget bar */}
      <CostMeter
        cost={{
          usd: data.totalCostUsd,
          inputTokens: data.totalInputTokens,
          outputTokens: data.totalOutputTokens,
        }}
        budgetUsd={data.budgetUsd > 0 ? data.budgetUsd : null}
        className="max-w-sm"
      />

      {/* Breach indicator — explicit "80% of budget" call-out per breached_80 */}
      {tone && data.breached80 && (
        <div
          role="alert"
          className="flex items-center gap-2 rounded-md border border-destructive/30 bg-destructive/8 px-3 py-2 text-sm text-destructive"
        >
          <span className="font-mono text-[11px] uppercase tracking-widest">
            80% of budget
          </span>
          <span className="text-muted-foreground">
            Tenant LLM spend has crossed the budget-utilization threshold.
          </span>
        </div>
      )}

      {/* Per-(agent, model) breakdown table */}
      {data.rows.length === 0 ? (
        <EmptyState
          icon={Coins}
          title="No spend in this window"
          description="No agent runs have incurred LLM cost in the selected window."
        />
      ) : (
        <div className="overflow-hidden rounded-xl border border-line-soft bg-panel-elevated shadow-[0_1px_0_oklch(1_0_0_/_0.04)_inset,0_8px_20px_-8px_oklch(0_0_0_/_0.35)]">
          <div className="flex flex-wrap items-center gap-2 border-b border-line-soft px-5 py-3.5">
            <span className="font-display text-[13.5px] font-bold tracking-[-0.01em]">
              Breakdown
            </span>
            <span className="font-mono text-[10.5px] text-muted-foreground">
              {rows.length} row{rows.length === 1 ? "" : "s"}
            </span>
            {agent !== "all" && (
              <span className="font-mono text-[10.5px] text-muted-foreground">
                · {formatUsd(filteredUsd)} of {formatUsd(data.totalCostUsd)}
              </span>
            )}
            <Select value={agent} onValueChange={setAgent}>
              <SelectTrigger className="ml-auto h-8 w-48 border-line-soft" aria-label="Filter by agent">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All agents</SelectItem>
                {agents.map((a) => (
                  <SelectItem key={a} value={a}>
                    {PHASE_LABEL[a as keyof typeof PHASE_LABEL] ?? a}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Agent</TableHead>
                <TableHead>Model</TableHead>
                <TableHead className="text-right">In</TableHead>
                <TableHead className="text-right">Out</TableHead>
                <TableHead className="text-right">Calls</TableHead>
                <TableHead className="text-right">Cost</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((row) => (
                <TableRow key={rowKey(row)}>
                  <TableCell className="text-[12px] font-medium">
                    {PHASE_LABEL[row.agentType] ?? row.agentType}
                  </TableCell>
                  <TableCell className="font-mono text-[12px]">
                    {row.model}
                  </TableCell>
                  <TableCell className="text-right font-mono text-[12px]">
                    {formatTokens(row.inputTokens)}
                  </TableCell>
                  <TableCell className="text-right font-mono text-[12px]">
                    {formatTokens(row.outputTokens)}
                  </TableCell>
                  <TableCell className="text-right font-mono text-[12px]">
                    {row.callCount}
                  </TableCell>
                  <TableCell className="text-right font-mono text-[12px] font-semibold">
                    {formatUsd(row.costUsd)}
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
