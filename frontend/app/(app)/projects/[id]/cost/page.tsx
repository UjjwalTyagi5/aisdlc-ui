"use client";

import * as React from "react";
import { useParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CircleDollarSign, Coins, Info, Loader2, TrendingUp, X } from "lucide-react";
import { toast } from "sonner";

import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { LoadingState } from "@/components/ui/loading-state";
import { ErrorState } from "@/components/ui/error-state";
import { RestrictedAccess } from "@/components/auth/restricted-access";
import { StatTile, StatTileGrid, budgetTone, usd } from "@/components/app/stat-tile";
import {
  BudgetWindowFieldsInput,
  BudgetWindowSummary,
} from "@/components/app/budget-window-fields";
import { BudgetAllocationNotice } from "@/components/app/budget-allocation-notice";
import { useCanSeeProjectCost } from "@/hooks/use-can-see-project-cost";
import { useSession } from "@/hooks/use-session";
import { useActiveWorkspace } from "@/hooks/use-workspaces";
import { effectivePlatformRole } from "@/lib/auth/effective-role";
import { getProject, listProjects, updateProject } from "@/lib/api/projects";
import { listRuns } from "@/lib/api/runs";
import { qk } from "@/lib/api/query-keys";
import { budgetAllocation } from "@/lib/budget-allocation";
import { budgetWindowError, type BudgetWindow } from "@/lib/schemas/budget-window";
import { PHASE_LABEL } from "@/lib/agents";
import { agentsForTrack } from "@/lib/tracks";
import type { Phase, ProjectId } from "@/lib/schemas";

/**
 * Project Cost — PRD §32.1 and §34.5.
 *
 * "Cost visibility is deliberately not a privilege — every builder sees their
 * own project's spend read-only; only admins change caps." (§34.5)
 *
 * Spend means model tokens. MCP tool calls land on the project's model spend,
 * connectors are billed by their own vendor, and there is no separate MCP
 * budget. Caps nest and never exceed their parent.
 */
export default function ProjectCostPage() {
  const params = useParams<{ id: string }>();
  const id = params.id as ProjectId;
  const session = useSession({ required: true });
  const canSeeCost = useCanSeeProjectCost(id);
  const role = effectivePlatformRole(session);

  const projectQ = useQuery({
    queryKey: qk.projects.detail(id),
    queryFn: () => getProject(id),
  });

  const runsQ = useQuery({
    queryKey: qk.runs.forProject(id),
    queryFn: () => listRuns({ projectId: id, pageSize: 100 }),
  });

  if (projectQ.isLoading) {
    return (
      <div className="p-4 md:px-10 md:py-8">
        <LoadingState variant="card" />
      </div>
    );
  }

  if (projectQ.isError || !projectQ.data) {
    return (
      <div className="p-4 md:px-10 md:py-8">
        <ErrorState title="Couldn't load the project" onRetry={() => projectQ.refetch()} />
      </div>
    );
  }

  const project = projectQ.data;

  if (!canSeeCost) {
    return (
      <RestrictedAccess description="Project spend is visible to this project's admin, its business unit admin, and organization admins." />
    );
  }

  const runs = runsQ.data?.items ?? [];

  const spend = project.monthlySpendUsd ?? 0;
  const cap = project.monthlyBudgetUsd ?? 0;
  const ratio = cap > 0 ? spend / cap : 0;

  // Spend by agent — attribution at the agent level (PRD FR-09).
  const roster = agentsForTrack(project.track);
  const byAgent = new Map<Phase, { usd: number; runs: number }>();
  for (const r of runs) {
    const key = r.phase as Phase;
    const prev = byAgent.get(key) ?? { usd: 0, runs: 0 };
    byAgent.set(key, { usd: prev.usd + r.cost.usd, runs: prev.runs + 1 });
  }
  const agentRows = roster
    .map((p) => ({ phase: p, ...(byAgent.get(p) ?? { usd: 0, runs: 0 }) }))
    .filter((r) => r.runs > 0)
    .sort((a, b) => b.usd - a.usd);

  const maxAgentSpend = Math.max(1, ...agentRows.map((r) => r.usd));
  const tokens = runs.reduce(
    (acc, r) => acc + r.cost.inputTokens + r.cost.outputTokens,
    0,
  );

  // Only admins change caps (§34.5).
  const canSetCap =
    role === "org_admin" || role === "bu_admin" || role === "project_admin";

  return (
    <div className="w-full space-y-5 p-4 md:px-10 md:py-8">
      <div>
        <h2 className="font-display text-lg font-semibold tracking-tight">Cost</h2>
        <p className="text-muted-foreground mt-1 max-w-2xl text-[13px]">
          This project&apos;s total spend against its budget. Spend means model tokens —
          MCP tool calls land here too; connectors are billed by their own
          vendor.
        </p>
      </div>

      <StatTileGrid>
        <StatTile
          label="Spend to date"
          value={usd(spend)}
          sub={cap > 0 ? `of ${usd(cap)} total budget` : "no budget set"}
          icon={CircleDollarSign}
          progress={cap > 0 ? ratio : undefined}
          tone={budgetTone(spend, cap)}
        />
        <StatTile
          label="Headroom"
          value={cap > 0 ? usd(Math.max(0, cap - spend)) : "—"}
          sub={cap > 0 ? `${Math.round((1 - Math.min(1, ratio)) * 100)}% remaining` : undefined}
          icon={TrendingUp}
          tone={budgetTone(spend, cap)}
        />
        <StatTile
          label="Workstreams"
          value={String(runs.length)}
          sub="this project, all stages"
          icon={Coins}
        />
        <StatTile
          label="Tokens"
          value={tokens.toLocaleString("en-US")}
          sub="input + output"
        />
      </StatTileGrid>

      {/* Cap state — the warn / hard-stop behaviour is explicit (PRD §34.5, §37) */}
      {cap > 0 && ratio >= 0.8 && (
        <div
          className={cn(
            "flex items-start gap-2 rounded-lg border px-4 py-3 text-[12.5px]",
            ratio >= 1
              ? "border-destructive/40 bg-destructive/5 text-destructive"
              : "border-warning/40 bg-warning/5 text-warning",
          )}
        >
          <Info className="mt-px size-4 shrink-0" aria-hidden />
          <p>
            {ratio >= 1 ? (
              <>
                This project is over its total cap. The next run is stopped,
                scoped to the cap that was hit — in-flight work is never killed.
                Request headroom to continue; it escalates one tier at a time.
              </>
            ) : (
              <>
                This project is at {Math.round(ratio * 100)}% of its total cap.
                At the cap, the business unit&apos;s policy decides whether to
                warn and escalate, or hard-stop the next run.
              </>
            )}
          </p>
        </div>
      )}

      {/* ── Spend by agent ────────────────────────────────────────────────── */}
      <section className="border-line-soft bg-panel-elevated rounded-xl border">
        <div className="border-line-soft border-b px-4 py-3">
          <h3 className="font-display text-[13px] font-semibold tracking-tight">
            Spend by agent
          </h3>
          <p className="text-muted-foreground mt-0.5 text-[12px]">
            Attribution down to the agent that consumed it.
          </p>
        </div>

        {runsQ.isLoading ? (
          <div className="p-4">
            <LoadingState variant="list" rows={4} />
          </div>
        ) : agentRows.length === 0 ? (
          <p className="text-muted-foreground px-4 py-6 text-[13px]">
            No agent runs have consumed budget on this project yet.
          </p>
        ) : (
          <ul className="divide-line-soft divide-y">
            {agentRows.map((r) => (
              <li key={r.phase} className="flex items-center gap-4 px-4 py-2.5">
                <span className="w-40 shrink-0 text-[12.5px]">
                  {PHASE_LABEL[r.phase]}
                </span>
                <span className="bg-surface-2 h-1.5 flex-1 overflow-hidden rounded-full">
                  <span
                    className="bg-primary block h-full rounded-full"
                    style={{ width: `${(r.usd / maxAgentSpend) * 100}%` }}
                  />
                </span>
                <span className="text-muted-foreground w-12 shrink-0 text-right font-mono text-[11px]">
                  {r.runs} run{r.runs === 1 ? "" : "s"}
                </span>
                <span className="w-20 shrink-0 text-right font-mono text-[12px]">
                  {usd(r.usd)}
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>

      <ProjectBudgetCard
        projectId={id}
        workspaceId={project.workspaceId ?? null}
        budget={project.monthlyBudgetUsd ?? null}
        window={{
          budgetStartDate: project.budgetStartDate ?? null,
          budgetEndDate: project.budgetEndDate ?? null,
        }}
        canSetCap={canSetCap}
      />

      <p className="text-muted-foreground text-[12.5px]">
        {canSetCap
          ? "You can set this project's cap and the period it covers. Allocating past the business unit's cap is allowed — it warns rather than blocks."
          : "Spend is read-only for your role. Only admins change caps."}
      </p>
    </div>
  );
}

/**
 * The project's own cap, and the window it applies to.
 *
 * Set at creation by the Project Admin (create-project-dialog.tsx) and raised
 * here afterwards — the two entry points write the same three fields, which is
 * why they share `BudgetWindowFieldsInput` and `budgetAllocation` rather than
 * each doing their own arithmetic.
 */
function ProjectBudgetCard({
  projectId,
  workspaceId,
  budget,
  window,
  canSetCap,
}: {
  projectId: ProjectId;
  workspaceId: string | null;
  budget: number | null;
  window: BudgetWindow;
  canSetCap: boolean;
}) {
  const queryClient = useQueryClient();
  const [editing, setEditing] = React.useState(false);
  const [value, setValue] = React.useState(budget !== null ? String(budget) : "");
  const [start, setStart] = React.useState(window.budgetStartDate ?? "");
  const [end, setEnd] = React.useState(window.budgetEndDate ?? "");

  React.useEffect(() => {
    setValue(budget !== null ? String(budget) : "");
    setStart(window.budgetStartDate ?? "");
    setEnd(window.budgetEndDate ?? "");
  }, [budget, window.budgetStartDate, window.budgetEndDate]);

  const { workspaces } = useActiveWorkspace();
  const siblingsQ = useQuery({
    queryKey: qk.projects.list({ pageSize: 200 }),
    queryFn: () => listProjects({ pageSize: 200 }),
    enabled: editing && !!workspaceId,
    staleTime: 60_000,
  });

  const proposed = value.trim() === "" ? null : Number(value);
  const allocation = budgetAllocation(
    workspaces.find((w) => w.id === workspaceId)?.monthlyBudgetUsd ?? null,
    // Excluding this project: its current cap must not count against the
    // proposal that replaces it (see lib/budget-allocation.ts).
    (siblingsQ.data?.items ?? []).filter(
      (p) => p.workspaceId === workspaceId && p.id !== projectId,
    ),
    Number.isFinite(proposed) ? proposed : null,
  );

  const windowError = budgetWindowError({ budgetStartDate: start, budgetEndDate: end });

  const mutation = useMutation({
    mutationFn: () =>
      updateProject(projectId, {
        monthlyBudgetUsd: proposed === 0 ? null : proposed,
        budgetStartDate: proposed === 0 ? null : start || null,
        budgetEndDate: proposed === 0 ? null : end || null,
      }),
    onSuccess: () => {
      toast.success("Budget updated");
      queryClient.invalidateQueries({ queryKey: qk.projects.detail(projectId) });
      queryClient.invalidateQueries({ queryKey: qk.projects.all() });
      setEditing(false);
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : "Couldn't update the budget"),
  });

  return (
    <section className="border-line-soft bg-panel-elevated rounded-2xl border px-5 py-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <Coins className="text-brand-bright size-4" aria-hidden />
          <span className="font-display text-[15px] font-bold tracking-[-0.01em]">
            Total budget
          </span>
        </div>
        {canSetCap && !editing && (
          <Button
            size="sm"
            variant="outline"
            className="border-line-soft h-7 font-mono text-[11px]"
            onClick={() => setEditing(true)}
          >
            {budget !== null ? "Edit budget" : "Set budget"}
          </Button>
        )}
      </div>

      {editing ? (
        <div className="mt-4 space-y-3">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-muted-foreground font-mono text-[13px]">$</span>
            <Input
              type="number"
              min={0}
              step="1"
              value={value}
              autoFocus
              onChange={(e) => setValue(e.target.value)}
              placeholder="0 = inherit the business unit's cap"
              className="border-line-soft h-8 w-52 font-mono text-[12px]"
            />
            <span className="text-muted-foreground font-mono text-[11px]">/ month</span>
            <Button
              size="sm"
              className="from-brand-gradient-from to-brand-gradient-to h-7 bg-gradient-to-br px-3 font-semibold text-white"
              disabled={mutation.isPending || windowError !== null}
              onClick={() => mutation.mutate()}
            >
              {mutation.isPending ? <Loader2 className="size-3.5 animate-spin" /> : "Save"}
            </Button>
            <Button size="sm" variant="ghost" className="h-7 w-7 p-0" onClick={() => setEditing(false)}>
              <X className="size-3.5" aria-hidden />
            </Button>
          </div>
          <BudgetWindowFieldsInput
            start={start}
            end={end}
            onStartChange={setStart}
            onEndChange={setEnd}
            error={windowError}
          />
          <BudgetAllocationNotice allocation={allocation} />
        </div>
      ) : (
        <div className="mt-3 space-y-1.5">
          <p className="font-mono text-[12px]">
            {budget !== null
              ? `${usd(budget)} / month`
              : "No cap of its own — inherits the business unit's."}
          </p>
          <BudgetWindowSummary window={window} />
        </div>
      )}
    </section>
  );
}
