"use client";

import * as React from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { ChevronDown } from "lucide-react";

import { cn } from "@/lib/utils";
import { LoadingState } from "@/components/ui/loading-state";
import { ErrorState } from "@/components/ui/error-state";
import { SpendBarChart } from "@/components/app/spend-bar-chart";
import { getSpendSeries } from "@/lib/api/cost";
import { listRuns } from "@/lib/api/runs";
import { qk } from "@/lib/api/query-keys";
import { PHASE_LABEL } from "@/lib/agents";
import { agentsForTrack } from "@/lib/tracks";
import type { DeliveryTrack, Phase, ProjectId } from "@/lib/schemas";

const MONTHS = 6;

const usd = (n: number) =>
  n.toLocaleString(undefined, {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: n < 100 ? 2 : 0,
  });

/**
 * This project's spend, on its Overview — two questions, two forms, one panel.
 *
 *   TREND (bars over months)  "are we heading for the cap?"
 *   BY AGENT (bars by phase)  "where is it going?"
 *
 * They are stacked rather than merged because they are different measures on
 * different scales, and putting dollars-per-month and dollars-per-agent on one
 * pair of axes would be a dual-axis chart — the single most misread chart there
 * is. Two plots that each own their axis say the same thing without the trap.
 *
 * FORM. Both are magnitude comparisons, so both are bars. Time runs left to
 * right along the bottom, which is the only orientation a month axis reads in;
 * agents are ranked by spend and run horizontally, because "Documentation" and
 * "Discovery & Assessment" are labels that do not fit under a vertical bar and
 * a ranked list is read top-down anyway.
 *
 * COLOUR. One hue throughout, `--series-1`, the same token the dashboard's
 * chart starts from (see spend-bar-chart.tsx for the palette rationale and its
 * validator results). Neither plot has more than one series, so there is
 * nothing for a second hue to distinguish — and colouring the agent bars by
 * rank would be colour carrying information the bar length already carries,
 * while implying the palette means something it does not. Length is the
 * encoding; colour is identity, and there is one identity here.
 */
export function ProjectCostPanel({
  projectId,
  projectName,
  track,
  monthlyBudgetUsd,
  monthlySpendUsd,
  className,
}: {
  projectId: ProjectId;
  projectName: string;
  track: DeliveryTrack;
  monthlyBudgetUsd: number | null;
  monthlySpendUsd: number;
  className?: string;
}) {
  // One project's own series, asked for by id — see buildSpendSeries's
  // `projectId` note on why this is not a client-side pick from every project.
  const seriesQ = useQuery({
    queryKey: qk.cost.spendSeries("project", "all", MONTHS, String(projectId)),
    queryFn: () => getSpendSeries({ groupBy: "project", months: MONTHS, projectId }),
    staleTime: 60_000,
  });

  const runsQ = useQuery({
    queryKey: qk.runs.forProject(projectId),
    queryFn: () => listRuns({ projectId, pageSize: 100 }),
    staleTime: 30_000,
  });

  /**
   * Spend attributed to the agent that consumed it (PRD FR-09), over this
   * project's own roster so a phase outside the track never appears.
   *
   * ATTRIBUTED, NOT SUMMED. The runs feed carries per-run LLM cost, which is
   * cents; the project's `monthlySpendUsd` is thousands. Summing runs directly
   * put "$0.11" under a heading that had just said "$2,688" — two numbers in
   * one panel, four orders of magnitude apart, both claiming to be this
   * project's spend. Whichever is right, a reader can only conclude the panel
   * is broken.
   *
   * So the month's spend is divided across agents in proportion to the cost
   * they actually drew: the breakdown answers "where did the $2,688 go" and
   * sums back to it, which is the only reason to put the two halves together.
   * The run counts beside each bar stay raw — they are observations, not a
   * share of anything.
   */
  const agentRows = React.useMemo(() => {
    const runs = runsQ.data?.items ?? [];
    const byAgent = new Map<Phase, { usd: number; runs: number }>();
    for (const r of runs) {
      const key = r.phase as Phase;
      const prev = byAgent.get(key) ?? { usd: 0, runs: 0 };
      byAgent.set(key, { usd: prev.usd + r.cost.usd, runs: prev.runs + 1 });
    }

    // The rows that will actually be RENDERED, before any scaling.
    const shown = agentsForTrack(track)
      .map((phase) => ({ phase, ...(byAgent.get(phase) ?? { usd: 0, runs: 0 }) }))
      .filter((r) => r.runs > 0);

    // Weighted over `shown`, not over every run. A run on a phase outside this
    // track's roster is dropped from the list but was still inflating the
    // denominator, so the bars summed to slightly less than the heading —
    // $2,680 under a heading reading $2,688, which is precisely the kind of
    // small unexplained gap that makes a reader distrust the whole panel.
    const observed = shown.reduce((a, r) => a + r.usd, 0);
    // Nothing to weight by, or no month total to spread: leave the figures as
    // they came rather than inventing a distribution.
    const scale = observed > 0 && monthlySpendUsd > 0 ? monthlySpendUsd / observed : 1;

    return shown
      .map((r) => ({ ...r, usd: r.usd * scale }))
      .sort((a, b) => b.usd - a.usd);
  }, [runsQ.data, track, monthlySpendUsd]);

  const ratio =
    monthlyBudgetUsd && monthlyBudgetUsd > 0 ? monthlySpendUsd / monthlyBudgetUsd : null;

  // Expanded by default — the plots are why the panel is here, and a card that
  // opens shut makes the reader work to find out it had anything in it. Collapse
  // exists for the reader who has read it and wants the runs below back up the
  // page. Deliberately not persisted: a preference remembered across projects
  // would hide this project's spend because of a decision made about another.
  const [open, setOpen] = React.useState(true);

  return (
    <section
      aria-labelledby="project-cost-heading"
      className={cn("border-line-soft bg-panel-elevated rounded-xl border", className)}
    >
      <div className="border-line-soft flex flex-wrap items-baseline gap-x-3 gap-y-1 border-b px-4 py-3">
        <h2
          id="project-cost-heading"
          className="font-display text-[13px] font-semibold tracking-tight"
        >
          Cost
        </h2>
        {/* The headline stays visible when collapsed: the figure is the one
            thing worth keeping on screen, and a collapsed card showing only the
            word "Cost" tells nobody whether it is worth opening. */}
        <span className="font-mono text-[13px] font-semibold tabular-nums">
          {usd(monthlySpendUsd)}
        </span>
        <span className="text-muted-foreground font-mono text-[11px]">
          {monthlyBudgetUsd && monthlyBudgetUsd > 0
            ? `of ${usd(monthlyBudgetUsd)} cap${ratio !== null ? ` · ${Math.round(ratio * 100)}%` : ""}`
            : "inherits the business unit's cap"}
        </span>
        <div className="ml-auto flex items-center gap-3">
          <Link
            href={`/projects/${projectId}/cost`}
            className="text-muted-foreground hover:text-foreground font-mono text-[11px] transition-colors"
          >
            Cost detail →
          </Link>
          <button
            type="button"
            onClick={() => setOpen((v) => !v)}
            aria-expanded={open}
            aria-controls="project-cost-body"
            className="text-muted-foreground hover:text-foreground hover:bg-surface-2 focus-visible:ring-ring -my-1 inline-flex size-6 items-center justify-center rounded-md transition-colors focus-visible:ring-2 focus-visible:outline-none"
          >
            <ChevronDown
              className={cn("size-4 transition-transform", !open && "-rotate-90")}
              aria-hidden
            />
            <span className="sr-only">{open ? "Collapse" : "Expand"} cost</span>
          </button>
        </div>
      </div>

      <div id="project-cost-body" hidden={!open}>
      {/* ── Trend ─────────────────────────────────────────────────────────── */}
      <div className="px-4 py-4">
        <p className="text-muted-foreground mb-3 font-mono text-[10px] tracking-[0.14em] uppercase">
          Last {MONTHS} months
        </p>
        {seriesQ.isError ? (
          <ErrorState title="Couldn't load spend" onRetry={() => seriesQ.refetch()} />
        ) : !seriesQ.data ? (
          <LoadingState variant="card" />
        ) : seriesQ.data.series.length === 0 ? (
          <p className="text-muted-foreground py-6 text-center font-mono text-[12px]">
            No spend recorded for {projectName} yet.
          </p>
        ) : (
          <SpendBarChart months={seriesQ.data.months} series={seriesQ.data.series} />
        )}
      </div>

      {/* ── By agent ──────────────────────────────────────────────────────── */}
      <div className="border-line-soft border-t px-4 py-4">
        <p className="text-muted-foreground mb-3 font-mono text-[10px] tracking-[0.14em] uppercase">
          By agent
        </p>
        {runsQ.isError ? (
          <ErrorState title="Couldn't load agent spend" onRetry={() => runsQ.refetch()} />
        ) : runsQ.isLoading ? (
          <LoadingState variant="list" rows={4} />
        ) : agentRows.length === 0 ? (
          <p className="text-muted-foreground py-4 text-[12.5px]">
            No agent has consumed budget on this project yet.
          </p>
        ) : (
          <AgentSpendBars rows={agentRows} />
        )}
      </div>
      </div>
    </section>
  );
}

/**
 * Ranked horizontal bars, one per agent that has spent.
 *
 * Scaled against the LARGEST bar rather than the project's cap: the question
 * here is which agent dominates, and against a cap that nothing is close to,
 * every bar collapses into a stub and the ranking becomes unreadable. The cap
 * comparison is the plot above, where it belongs.
 *
 * Every bar is direct-labelled with its dollar figure — the exception to "never
 * a number on every mark" that a ranked list of eight earns, because reading a
 * value off a horizontal bar against no gridline is otherwise guesswork. The
 * label is text-coloured, never the series colour.
 */
function AgentSpendBars({ rows }: { rows: Array<{ phase: Phase; usd: number; runs: number }> }) {
  // Rows arrive sorted, so the first IS the max. Deliberately not
  // `Math.max(…, 1)`: a $1 floor is invisible while spend is in the thousands
  // and silently wrong below it — with a top agent at $0.11 every bar was
  // scaled against $1 and the leader rendered at 11% of its own track.
  const top = rows[0]?.usd ?? 0;
  const max = top > 0 ? top : 1;

  return (
    <ul className="space-y-2">
      {rows.map((r) => {
        const pct = (r.usd / max) * 100;
        return (
          <li key={r.phase} className="grid grid-cols-[8.5rem_1fr_auto] items-center gap-3">
            <span className="truncate text-[12.5px]">{PHASE_LABEL[r.phase]}</span>

            <span className="bg-surface-2 relative h-2 overflow-hidden rounded-full">
              <span
                className="absolute inset-y-0 left-0 rounded-full"
                style={{ width: `${Math.max(pct, 1.5)}%`, background: "var(--series-1)" }}
              />
            </span>

            {/* Money only. The run count sat here and answered a question
                nobody asked of a cost breakdown — and at "1 run" against
                $1,101 it invited exactly the wrong inference, since the figure
                is this month's spend attributed to the agent, not the price of
                the one run the feed happens to be showing. */}
            <span className="w-16 text-right font-mono text-[12px] font-semibold tabular-nums">
              {usd(r.usd)}
            </span>
          </li>
        );
      })}
    </ul>
  );
}
