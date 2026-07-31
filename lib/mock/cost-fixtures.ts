/**
 * Dummy cost/budget data for the Cost & Budget screen — derived entirely from
 * the real workspace and project fixtures (mocks/fixtures.ts,
 * lib/mock/workspace-fixtures.ts) so these numbers never contradict what the
 * Dashboard already shows. Plain data + pure functions, server-safe (imported
 * by the app/api/cost route handlers). This is the DUMMY-DATA source; the
 * Langfuse-backed cost pipeline (Phase 2) replaces the route-handler bodies,
 * not these shapes.
 */
import { PROJECTS } from "@/mocks/fixtures";
import { listWorkspaces } from "@/lib/mock/workspace-fixtures";
import type { BudgetRow, CostBreakdown, CostBreakdownRow } from "@/lib/api/cost";

const GENERATED_AT = new Date(Date.UTC(2026, 5, 17, 12, 0, 0)).toISOString();

/**
 * @param allowed the ids the viewer may read, or null when unbounded. The `org`
 *   row is deliberately recomputed from the FILTERED unit rows rather than kept
 *   as the true organisation total: leaving it whole would hand a Business Unit
 *   Admin the org-wide figure, and every sibling unit's spend is then a single
 *   subtraction away.
 */
export function buildBudgets(
  allowed?: { workspaceIds: string[]; projectIds: string[] } | null,
): {
  org: BudgetRow;
  workspaces: BudgetRow[];
  projects: BudgetRow[];
  defaultProjectBudgetUsd: number;
  generatedAt: string;
} {
  const workspaces = listWorkspaces().filter(
    (w) => allowed == null || allowed.workspaceIds.includes(String(w.id)),
  );

  const workspaceRows: BudgetRow[] = workspaces.map((w) => ({
    scope: "workspace",
    id: w.id,
    name: w.displayName,
    monthlyBudgetUsd: w.monthlyBudgetUsd,
    monthlySpendUsd: w.monthlySpendUsd,
  }));

  const projectRows: BudgetRow[] = PROJECTS.filter(
    (p) => !p.archived && (allowed == null || allowed.projectIds.includes(String(p.id))),
  ).map((p) => ({
    scope: "project",
    id: p.id,
    name: p.name,
    monthlyBudgetUsd: p.monthlyBudgetUsd ?? null,
    monthlySpendUsd: p.monthlySpendUsd ?? 0,
  }));

  const orgBudget = workspaceRows.reduce((a, w) => a + (w.monthlyBudgetUsd ?? 0), 0);
  const orgSpend = workspaceRows.reduce((a, w) => a + w.monthlySpendUsd, 0);

  return {
    org: {
      scope: "org",
      id: "org_acme",
      name: "ABC Bank",
      monthlyBudgetUsd: orgBudget,
      monthlySpendUsd: Number(orgSpend.toFixed(2)),
    },
    workspaces: workspaceRows,
    projects: projectRows,
    defaultProjectBudgetUsd: 4000,
    generatedAt: GENERATED_AT,
  };
}

/** What the spend chart splits its bars by. */
export type SpendGroupBy = "business_unit" | "project" | "model";

/** `YYYY-MM` labels ending at the current month, oldest first. */
function monthLabels(months: number): string[] {
  const end = new Date(GENERATED_AT);
  const labels: string[] = [];
  for (let i = months - 1; i >= 0; i--) {
    const d = new Date(Date.UTC(end.getUTCFullYear(), end.getUTCMonth() - i, 1));
    labels.push(`${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, "0")}`);
  }
  return labels;
}

/**
 * Spread a known current-month figure backwards over `months`.
 *
 * The current month is returned untouched so the chart's last bar equals the
 * figure printed beside it — a chart that disagreed with its own tile reads as
 * a bug in the tile. Earlier months ramp back ~8% each with a small
 * series-specific wobble, deterministic rather than random: the same page
 * rendered twice must not draw two different histories.
 */
function backcast(current: number, months: number, seriesIndex: number): number[] {
  return Array.from({ length: months }, (_, i) => {
    const age = months - 1 - i;
    if (age === 0) return Number(current.toFixed(2));
    const ramp = Math.pow(1 / 1.08, age);
    const wobble = 1 + 0.06 * Math.sin((i + seriesIndex * 2) * 1.1);
    return Number((current * ramp * wobble).toFixed(2));
  });
}

/**
 * Monthly spend, split by Business Unit, project or model.
 *
 * All three groupings are derived from the same fixture figures rather than
 * seeded apart, so they reconcile: the per-project bars for a unit sum to that
 * unit's bar, and the per-model split is that same total redistributed by
 * MODEL_SHARE. Grouping is a change of lens, not a change of subject.
 *
 * @param allowedWorkspaceIds units the viewer may read, or null when unbounded.
 * @param workspaceId         the viewer's narrowing choice ("all" → no filter),
 *                            applied on top of what they're allowed to see.
 */
export function buildSpendSeries(
  months = 6,
  allowedWorkspaceIds?: string[] | null,
  groupBy: SpendGroupBy = "business_unit",
  workspaceId?: string | null,
): {
  months: string[];
  groupBy: SpendGroupBy;
  series: Array<{ id: string; name: string; points: number[] }>;
} {
  const labels = monthLabels(months);

  const readable = listWorkspaces().filter(
    (w) => allowedWorkspaceIds == null || allowedWorkspaceIds.includes(String(w.id)),
  );
  const scoped = workspaceId ? readable.filter((w) => String(w.id) === workspaceId) : readable;
  const scopedIds = new Set(scoped.map((w) => String(w.id)));

  if (groupBy === "business_unit") {
    return {
      months: labels,
      groupBy,
      series: scoped.map((w, i) => ({
        id: String(w.id),
        name: w.displayName,
        points: backcast(w.monthlySpendUsd, months, i),
      })),
    };
  }

  if (groupBy === "project") {
    const projects = PROJECTS.filter(
      (p) => !p.archived && p.workspaceId != null && scopedIds.has(String(p.workspaceId)),
    );
    return {
      months: labels,
      groupBy,
      series: projects
        // Largest first, so the top-N fold keeps the spend that matters.
        .sort((a, b) => (b.monthlySpendUsd ?? 0) - (a.monthlySpendUsd ?? 0))
        .map((p, i) => ({
          id: String(p.id),
          name: p.name,
          points: backcast(p.monthlySpendUsd ?? 0, months, i),
        })),
    };
  }

  // Model: the scoped total redistributed by the same share table the Cost
  // page's per-model breakdown uses, so the two agree.
  const total = scoped.reduce((a, w) => a + w.monthlySpendUsd, 0);
  return {
    months: labels,
    groupBy,
    series: MODEL_SHARE.map(({ model, share }, i) => ({
      id: model,
      name: model,
      points: backcast(total * share, months, i),
    })),
  };
}

/** Per-model breakdown, weighted toward the models already used across fixtures/traces. */
const MODEL_SHARE: Array<{ model: string; share: number }> = [
  { model: "claude-sonnet-4-6", share: 0.62 },
  { model: "claude-opus-4-7", share: 0.23 },
  { model: "claude-haiku-4-5", share: 0.15 },
];

/**
 * @param workspaceId          the caller's narrowing choice (a Business Unit
 *                             filter on the Cost page), or null for "all".
 * @param allowedWorkspaceIds  the units the viewer may read, or null when
 *                             unbounded. Applied AFTER `workspaceId`, so "all"
 *                             can only ever mean "all of mine" — otherwise a
 *                             Business Unit Admin's total spend would silently
 *                             include every sibling unit's bill.
 */
export function buildCostBreakdown(
  windowDays: number,
  workspaceId?: string | null,
  allowedWorkspaceIds?: string[] | null,
): CostBreakdown {
  const workspaces = listWorkspaces();
  let scoped = workspaceId ? workspaces.filter((w) => w.id === workspaceId) : workspaces;
  if (allowedWorkspaceIds != null) {
    scoped = scoped.filter((w) => allowedWorkspaceIds.includes(String(w.id)));
  }
  const totalCostUsd = Number(scoped.reduce((a, w) => a + w.monthlySpendUsd, 0).toFixed(2));
  const budgetUsd = scoped.reduce((a, w) => a + (w.monthlyBudgetUsd ?? 0), 0);

  const rows: CostBreakdownRow[] = MODEL_SHARE.map(({ model, share }) => {
    const costUsd = Number((totalCostUsd * share).toFixed(2));
    // ~68% of spend is input tokens at this fixture's blended price point.
    const inputTokens = Math.round((costUsd * 0.68 * 1_000_000) / 3);
    const outputTokens = Math.round((costUsd * 0.32 * 1_000_000) / 15);
    return {
      model,
      inputTokens,
      outputTokens,
      costUsd,
      callCount: Math.max(1, Math.round(costUsd * 4)),
    };
  });

  return {
    windowDays,
    totalCostUsd,
    totalInputTokens: rows.reduce((a, r) => a + r.inputTokens, 0),
    totalOutputTokens: rows.reduce((a, r) => a + r.outputTokens, 0),
    rows,
    generatedAt: GENERATED_AT,
    budgetUsd,
    utilization: budgetUsd > 0 ? Number((totalCostUsd / budgetUsd).toFixed(3)) : 0,
    breached80: budgetUsd > 0 && totalCostUsd / budgetUsd >= 0.8,
  };
}
