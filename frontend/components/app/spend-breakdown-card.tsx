"use client";

import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { ChevronDown, Coins } from "lucide-react";
import Link from "next/link";

import { cn } from "@/lib/utils";
import { LoadingState } from "@/components/ui/loading-state";
import { ErrorState } from "@/components/ui/error-state";
import { SpendRankedBars } from "@/components/app/spend-bar-chart";
import { getSpendSeries } from "@/lib/api/cost";
import { qk } from "@/lib/api/query-keys";
import { useSession } from "@/hooks/use-session";
import { hasPermission } from "@/lib/auth/permissions";
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
 * Scoped by the server for anyone who may read spend at all, so this renders
 * unchanged for a Business Unit Admin and simply covers fewer rows — the endpoint
 * bounds "all" to the units the caller may read.
 *
 * BUT SCOPING IS NOT THE ONLY ANSWER THE SERVER GIVES. `cost:view` is held by
 * three roles (bu_admin, project_admin, security_engineer); GET /cost/spend-series
 * REFUSES everyone else. This card rendered on /projects with no permission check,
 * so a developer, BA, architect, QA, DevOps engineer, scrum master or contributor
 * opening Projects got a 403 turned into a red "Couldn't load spend" box — an
 * error about money they were never meant to see, on a page whose actual subject
 * loaded perfectly well beneath it.
 *
 * It renders nothing at all for them now. A permission limit is not a failure, and
 * spend is not this reader's business.
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
  const session = useSession();
  const mayReadSpend = hasPermission(session, "cost:view");

  const q = useQuery({
    queryKey: qk.cost.spendSeries(groupBy, "all", MONTHS),
    queryFn: () => getSpendSeries({ groupBy, months: MONTHS }),
    staleTime: 60_000,
    // Not merely hidden — never requested. Firing a query we know will 403 costs a
    // round trip and puts a permission refusal in the console on every page load.
    enabled: mayReadSpend,
  });

  const rows =
    q.data?.series.map((s) => ({
      id: s.id,
      name: s.name,
      // Last point is the current month — see backcast() in cost-fixtures.
      value: s.points[s.points.length - 1] ?? 0,
    })) ?? [];

  const heading = title ?? `Spend by ${SPEND_GROUP_BY_LABEL[groupBy].toLowerCase()}`;

  /**
   * Collapsible, and open by default.
   *
   * It is a reference figure, not a task — on a list of six projects it takes
   * a third of the fold before you reach the projects themselves. Defaulting
   * to open keeps it discoverable for anyone who has not formed a habit;
   * collapsing is remembered per breakdown so the people who scroll past it
   * every day only pay for it once.
   */
  const storageKey = `spend-breakdown:${groupBy}:collapsed`;
  const [collapsed, setCollapsed] = React.useState(false);
  React.useEffect(() => {
    setCollapsed(window.localStorage.getItem(storageKey) === "1");
  }, [storageKey]);
  const toggle = () => {
    setCollapsed((prev) => {
      const next = !prev;
      window.localStorage.setItem(storageKey, next ? "1" : "0");
      return next;
    });
  };

  // Nothing at all, not an empty state: a reader without cost:view is not missing
  // data, they are looking at a page that has no spend section.
  if (!mayReadSpend) return null;

  return (
    <section
      aria-labelledby={`spend-breakdown-${groupBy}`}
      className={cn("border-line-soft bg-panel-elevated rounded-xl border", className)}
    >
      <div
        className={cn(
          "flex items-center justify-between px-4 py-3",
          !collapsed && "border-line-soft border-b",
        )}
      >
        {/* The heading IS the toggle — a separate chevron button next to a
            non-clickable title gives the same action two hit areas of very
            different size, and people aim at the words. */}
        <button
          type="button"
          onClick={toggle}
          aria-expanded={!collapsed}
          aria-controls={`spend-breakdown-body-${groupBy}`}
          className="group flex items-center gap-2 text-left"
        >
          <ChevronDown
            className={cn(
              "text-muted-foreground size-3.5 shrink-0 transition-transform",
              collapsed && "-rotate-90",
            )}
            aria-hidden
          />
          <h2
            id={`spend-breakdown-${groupBy}`}
            className="font-display group-hover:text-brand-bright flex items-center gap-2 text-[13px] font-semibold tracking-tight transition-colors"
          >
            <Coins className="text-brand-bright size-3.5" aria-hidden />
            {heading}
          </h2>
        </button>
        <Link
          href="/cost"
          className="text-muted-foreground hover:text-foreground font-mono text-[11px] transition-colors"
        >
          Cost &amp; budget →
        </Link>
      </div>
      <div id={`spend-breakdown-body-${groupBy}`} hidden={collapsed} className="px-4 py-4">
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
