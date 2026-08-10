"use client";

import * as React from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";

import { cn } from "@/lib/utils";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { LoadingState } from "@/components/ui/loading-state";
import { ErrorState } from "@/components/ui/error-state";
import { SpendBarChart } from "@/components/app/spend-bar-chart";
import { getSpendSeries } from "@/lib/api/cost";
import { qk } from "@/lib/api/query-keys";
import { BUSINESS_UNIT_LABEL, BUSINESS_UNIT_LABEL_PLURAL } from "@/lib/scope";
import { SPEND_GROUP_BY_LABEL, type SpendGroupBy } from "@/lib/schemas/spend-series";

const MONTHS = 6;

/**
 * The spend chart plus the two controls that steer it.
 *
 * Filters live here rather than on the page because they own the query key —
 * changing one refetches only this panel, leaving the dashboard's tiles alone.
 * That is also why the series has its own endpoint: the tiles change when the
 * organisation does, this changes whenever someone clicks a dropdown.
 *
 * Scope is the server's job, not this component's. `/api/cost/spend-series`
 * bounds "all" to the units the caller may read, so a Business Unit Admin can
 * be shown exactly the same panel as an Org Admin and simply see less in it —
 * no second component, no role branch here.
 */
export function SpendPanel({
  workspaces,
  defaultGroupBy = "business_unit",
  className,
}: {
  /** Units offered in the filter — pass only what the viewer can read. */
  workspaces: Array<{ id: string; displayName: string }>;
  defaultGroupBy?: SpendGroupBy;
  className?: string;
}) {
  const [groupBy, setGroupBy] = React.useState<SpendGroupBy>(defaultGroupBy);
  const [workspaceId, setWorkspaceId] = React.useState<string>("all");

  const q = useQuery({
    queryKey: qk.cost.spendSeries(groupBy, workspaceId, MONTHS),
    queryFn: () => getSpendSeries({ groupBy, workspaceId, months: MONTHS }),
    staleTime: 60_000,
    // Keeps the previous chart on screen while the next one loads, so changing
    // a filter dims the bars rather than collapsing the panel to a spinner.
    placeholderData: (prev) => prev,
  });

  // With one unit there is nothing to narrow to, and a dropdown whose only
  // real option is "All" is furniture.
  const showUnitFilter = workspaces.length > 1;

  return (
    <section
      aria-labelledby="spend-panel-heading"
      className={cn("border-line-soft bg-panel-elevated rounded-xl border", className)}
    >
      <div className="border-line-soft flex flex-col gap-3 border-b px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
        <h2 id="spend-panel-heading" className="font-display text-[13px] font-semibold tracking-tight">
          Spend by {SPEND_GROUP_BY_LABEL[groupBy].toLowerCase()}
        </h2>

        <div className="flex flex-wrap items-center gap-2">
          <Select value={groupBy} onValueChange={(v) => setGroupBy(v as SpendGroupBy)}>
            <SelectTrigger
              aria-label="Group spend by"
              className="border-line-soft bg-surface-1 h-8 w-[9.5rem] font-mono text-[11px]"
            >
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {(Object.keys(SPEND_GROUP_BY_LABEL) as SpendGroupBy[]).map((g) => (
                <SelectItem key={g} value={g} className="text-[12px]">
                  By {SPEND_GROUP_BY_LABEL[g].toLowerCase()}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>

          {showUnitFilter && (
            <Select value={workspaceId} onValueChange={setWorkspaceId}>
              <SelectTrigger
                aria-label={`Filter by ${BUSINESS_UNIT_LABEL.toLowerCase()}`}
                className="border-line-soft bg-surface-1 h-8 w-[11rem] font-mono text-[11px]"
              >
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all" className="text-[12px]">
                  All {BUSINESS_UNIT_LABEL_PLURAL.toLowerCase()}
                </SelectItem>
                {workspaces.map((w) => (
                  <SelectItem key={w.id} value={w.id} className="text-[12px]">
                    {w.displayName}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}

          <Link
            href="/cost"
            className="text-muted-foreground hover:text-foreground font-mono text-[11px] transition-colors"
          >
            Cost &amp; budget →
          </Link>
        </div>
      </div>

      <div className="px-4 py-4">
        {q.isError ? (
          <ErrorState title="Couldn't load spend" onRetry={() => q.refetch()} />
        ) : !q.data ? (
          <LoadingState variant="card" />
        ) : q.data.series.length === 0 ? (
          <p className="text-muted-foreground py-8 text-center font-mono text-[12px]">
            No {SPEND_GROUP_BY_LABEL[groupBy].toLowerCase()} spend in this selection.
          </p>
        ) : (
          <div className={cn("transition-opacity", q.isFetching && "opacity-60")}>
            <SpendBarChart months={q.data.months} series={q.data.series} />
          </div>
        )}
      </div>
    </section>
  );
}
