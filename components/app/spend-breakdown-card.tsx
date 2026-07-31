"use client";

import { useQuery } from "@tanstack/react-query";
import { Coins } from "lucide-react";
import Link from "next/link";

import { cn } from "@/lib/utils";
import { LoadingState } from "@/components/ui/loading-state";
import { ErrorState } from "@/components/ui/error-state";
import { SpendRankedBars } from "@/components/app/spend-bar-chart";
import { getSpendSeries } from "@/lib/api/cost";
import { qk } from "@/lib/api/query-keys";
import { SPEND_GROUP_BY_LABEL, type SpendGroupBy } from "@/lib/schemas/spend-series";

const MONTHS = 6;

/**
 * "Where did this month's spend go", ranked — for a page whose subject IS the
 * breakdown: models on Model Management, projects on Projects.
 *
 * Current month only. These pages are asking which of the things on this
 * screen cost the most right now, not how that has moved; the time dimension
 * lives on the dashboard panel and the Cost page, and repeating it here would
 * make every page a dashboard.
 *
 * Scoped by the server, so this renders unchanged for a Business Unit Admin
 * and simply covers fewer rows — the endpoint bounds "all" to the units the
 * caller may read.
 */
export function SpendBreakdownCard({
  groupBy,
  title,
  className,
}: {
  groupBy: SpendGroupBy;
  title?: string;
  className?: string;
}) {
  const q = useQuery({
    queryKey: qk.cost.spendSeries(groupBy, "all", MONTHS),
    queryFn: () => getSpendSeries({ groupBy, months: MONTHS }),
    staleTime: 60_000,
  });

  const rows =
    q.data?.series.map((s) => ({
      id: s.id,
      name: s.name,
      // Last point is the current month — see backcast() in cost-fixtures.
      value: s.points[s.points.length - 1] ?? 0,
    })) ?? [];

  const heading = title ?? `Spend by ${SPEND_GROUP_BY_LABEL[groupBy].toLowerCase()}`;

  return (
    <section
      aria-labelledby={`spend-breakdown-${groupBy}`}
      className={cn("border-line-soft bg-panel-elevated rounded-xl border", className)}
    >
      <div className="border-line-soft flex items-center justify-between border-b px-4 py-3">
        <h2
          id={`spend-breakdown-${groupBy}`}
          className="font-display flex items-center gap-2 text-[13px] font-semibold tracking-tight"
        >
          <Coins className="text-brand-bright size-3.5" aria-hidden />
          {heading}
        </h2>
        <Link
          href="/cost"
          className="text-muted-foreground hover:text-foreground font-mono text-[11px] transition-colors"
        >
          Cost &amp; budget →
        </Link>
      </div>
      <div className="px-4 py-4">
        {q.isError ? (
          <ErrorState title="Couldn't load spend" onRetry={() => q.refetch()} />
        ) : q.isLoading ? (
          <LoadingState variant="list" rows={3} />
        ) : (
          <SpendRankedBars
            rows={rows}
            emptyLabel={`No ${SPEND_GROUP_BY_LABEL[groupBy].toLowerCase()} spend this month.`}
          />
        )}
      </div>
    </section>
  );
}
