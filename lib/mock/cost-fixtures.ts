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

/**
 * Monthly spend per Business Unit, ending at the current month.
 *
 * Derived from each unit's `monthlySpendUsd` rather than seeded independently,
 * so the last point of every series equals the figure the Dashboard tiles and
 * the Cost page already show — a chart that disagreed with the number printed
 * next to it would be read as a bug in the number.
 *
 * The shape backwards is deterministic (a fixed per-unit ramp plus a fixed
 * wobble keyed off the month index), not random: the same page rendered twice
 * must not draw two different histories.
 *
 * @param months how many points to return, including the current month.
 */
export function buildSpendSeries(
  months = 6,
  allowedWorkspaceIds?: string[] | null,
): {
  months: string[];
  series: Array<{ workspaceId: string; name: string; points: number[] }>;
} {
  const workspaces = listWorkspaces().filter(
    (w) => allowedWorkspaceIds == null || allowedWorkspaceIds.includes(String(w.id)),
  );

  const end = new Date(GENERATED_AT);
  const labels: string[] = [];
  for (let i = months - 1; i >= 0; i--) {
    const d = new Date(Date.UTC(end.getUTCFullYear(), end.getUTCMonth() - i, 1));
    labels.push(`${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, "0")}`);
  }

  const series = workspaces.map((w, wi) => {
    const current = w.monthlySpendUsd;
    const points = labels.map((_, i) => {
      const age = months - 1 - i; // 0 for the current month
      // The current month IS the live figure — no ramp, no wobble — so the
      // chart's last point matches the tile beside it exactly.
      if (age === 0) return Number(current.toFixed(2));
      // Earlier months ramp back ~8% each, with a small unit-specific wobble so
      // the lines don't read as three copies of one curve.
      const ramp = Math.pow(1 / 1.08, age);
      const wobble = 1 + 0.06 * Math.sin((i + wi * 2) * 1.1);
      return Number((current * ramp * wobble).toFixed(2));
    });
    return { workspaceId: String(w.id), name: w.displayName, points };
  });

  return { months: labels, series };
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
