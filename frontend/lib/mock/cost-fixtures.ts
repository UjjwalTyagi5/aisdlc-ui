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
import { providerLabel } from "@/lib/models/provider-labels";
import type { BudgetRow, CostBreakdown, CostBreakdownRow } from "@/lib/api/cost";
import type { Phase } from "@/lib/schemas/enums";

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
export type SpendGroupBy = "business_unit" | "project" | "model" | "provider";

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
 * @param projectId           a single project to report on, for a project's own
 *                            Overview. Implies `groupBy: "project"` and returns
 *                            at most one series. It exists so that page can ask
 *                            for one project rather than fetching every
 *                            project's series and picking one in the browser —
 *                            a client-side pick still ships the other projects'
 *                            spend to a viewer who has no reason to hold it.
 */
export function buildSpendSeries(
  months = 6,
  allowedWorkspaceIds?: string[] | null,
  groupBy: SpendGroupBy = "business_unit",
  workspaceId?: string | null,
  projectId?: string | null,
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

  // One named project — still bounded by the units the viewer may read, so an
  // id they cannot reach yields an empty series rather than that project's
  // numbers. The caller checks readability too and 404s; this is the backstop.
  if (projectId) {
    const project = PROJECTS.find(
      (p) =>
        String(p.id) === projectId &&
        p.workspaceId != null &&
        readable.some((w) => String(w.id) === String(p.workspaceId)),
    );
    return {
      months: labels,
      groupBy: "project",
      series: project
        ? [
            {
              id: String(project.id),
              name: project.name,
              points: backcast(project.monthlySpendUsd ?? 0, months, 0),
            },
          ]
        : [],
    };
  }

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

  // Model / provider: the scoped total redistributed by the same shares the
  // Cost page's breakdown sums to, so chart and table agree. Both read from
  // the agent × model matrix (see MODEL_SHARE) rather than a second table.
  const total = scoped.reduce((a, w) => a + w.monthlySpendUsd, 0);

  if (groupBy === "provider") {
    // Rolled up from the SAME per-model shares rather than seeded separately —
    // a provider total that disagreed with its own models summed would be the
    // exact failure the derived MODEL_SHARE exists to prevent.
    const byProvider = new Map<string, number>();
    for (const { model, share } of MODEL_SHARE) {
      const provider = providerOfModel(model);
      byProvider.set(provider, (byProvider.get(provider) ?? 0) + share);
    }
    return {
      months: labels,
      groupBy,
      series: [...byProvider.entries()]
        .sort((a, b) => b[1] - a[1])
        .map(([provider, share], i) => ({
          id: provider,
          // "other" is a bucket, not a slug, so it has no place in the shared
          // vendor table — but it still has to read as a word, not a key.
          name: provider === "other" ? "Other" : providerLabel(provider),
          points: backcast(total * share, months, i),
        })),
    };
  }

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

/**
 * Which provider a model id belongs to.
 *
 * Prefix matching rather than a lookup table: the catalogue grows, and a table
 * here would silently drop a newly-added model into no provider at all —
 * losing its spend from the provider view while the model view still showed
 * it, which is the disagreement this whole file works to avoid.
 */
function providerOfModel(modelId: string): string {
  // Resold families first: a Bedrock id is `anthropic.claude-…`, which the
  // `claude` test below would happily claim for Anthropic — attributing AWS's
  // invoice to a vendor the organisation may not even hold a contract with.
  // Routed families first — `azure/gpt-5.1` and `anthropic.claude-…` both name
  // a model some OTHER vendor resells, and the plain-family tests below would
  // hand each to the vendor whose model it is rather than the one billing for
  // it. That would put one contract's spend on another contract's page.
  const routed = modelId.match(/^(azure|vertex_ai|bedrock|openrouter)\//);
  if (routed) return routed[1]!;
  if (/^(anthropic|amazon|meta|mistral)\./.test(modelId)) return "bedrock";
  if (modelId.startsWith("claude")) return "anthropic";
  if (modelId.startsWith("gpt") || modelId.startsWith("o1")) return "openai";
  if (modelId.startsWith("gemini")) return "google";
  if (modelId.startsWith("grok")) return "xai";
  if (modelId.startsWith("mistral")) return "mistral";
  return "other";
}

/**
 * Per-agent breakdown, over the eight agents of the core pipeline.
 *
 * Weighted the way a real programme spends: Development dominates because it
 * generates the most tokens per run, Requirements and Design are front-loaded
 * but cheaper, and Documentation trails. Shares sum to 1 so the per-agent split
 * of any total is that total — the same discipline MODEL_SHARE follows, and the
 * reason the two can be crossed without either drifting from the headline.
 */
const AGENT_SHARE: Array<{ agentType: Phase; share: number }> = [
  { agentType: "development", share: 0.31 },
  { agentType: "testing", share: 0.17 },
  { agentType: "design", share: 0.14 },
  { agentType: "review", share: 0.12 },
  { agentType: "requirements", share: 0.1 },
  { agentType: "security", share: 0.08 },
  { agentType: "deployment", share: 0.05 },
  { agentType: "documentation", share: 0.03 },
];

/**
 * Which models an agent actually runs on.
 *
 * A uniform cross-product would be the wrong picture: it says every agent uses
 * every model in the same proportion, which erases the one insight this table
 * exists to give — that the expensive agents are expensive partly because of
 * what they run ON. Development leans on Opus, Documentation lives on Haiku,
 * and the reviewers sit in between. Weights are per-agent and sum to 1.
 */
const AGENT_MODEL_MIX: Record<Phase, Partial<Record<string, number>>> = {
  requirements: { "claude-sonnet-4-6": 0.75, "claude-haiku-4-5": 0.25 },
  design: { "claude-sonnet-4-6": 0.6, "claude-opus-4-7": 0.4 },
  development: { "claude-opus-4-7": 0.55, "claude-sonnet-4-6": 0.45 },
  review: { "claude-sonnet-4-6": 0.7, "claude-opus-4-7": 0.3 },
  security: { "claude-opus-4-7": 0.5, "claude-sonnet-4-6": 0.5 },
  testing: { "claude-sonnet-4-6": 0.65, "claude-haiku-4-5": 0.35 },
  deployment: { "claude-sonnet-4-6": 0.8, "claude-haiku-4-5": 0.2 },
  documentation: { "claude-haiku-4-5": 0.7, "claude-sonnet-4-6": 0.3 },
  // Track-specific agents draw no spend in these fixtures; listed so the record
  // stays exhaustive over Phase and a new phase cannot be forgotten here.
  discovery: {},
  strategy: {},
  migration_mapping: {},
  validation: {},
  data_engineering: {},
};

/**
 * Per-model breakdown — DERIVED from the agent × model matrix above, never
 * stated separately.
 *
 * It used to be its own hand-written table (sonnet .62 / opus .23 / haiku .15),
 * which was fine while the model split was the only split. The moment agents
 * got their own mix, two independent tables described one distribution and
 * promptly disagreed: the matrix puts Opus at 30% of spend because Development
 * and Security lean on it, while the standalone table still said 23%. Nothing
 * surfaced the contradiction — the Cost breakdown summed one way and the
 * dashboard's "by model" chart drew the other, from the same headline total.
 *
 * Deriving it means the agent mix is the single fact and every model figure in
 * the app is a view of it. Editing one agent's mix moves the chart too, which
 * is the only behaviour that can stay true.
 */
const MODEL_SHARE: Array<{ model: string; share: number }> = (() => {
  const acc = new Map<string, number>();
  for (const { agentType, share } of AGENT_SHARE) {
    for (const [model, weight] of Object.entries(AGENT_MODEL_MIX[agentType])) {
      acc.set(model, (acc.get(model) ?? 0) + share * (weight ?? 0));
    }
  }
  return [...acc.entries()]
    .sort((a, b) => b[1] - a[1])
    .map(([model, share]) => ({ model, share }));
})();

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

  // One row per (agent, model) the agent actually runs on. Crossing the two
  // share tables keeps every lens reconcilable: summing rows by model still
  // yields MODEL_SHARE of the total, summing by agent yields AGENT_SHARE, and
  // both sum to the headline. A breakdown whose columns disagreed with the tile
  // above them would read as a bug in the tile.
  const rows: CostBreakdownRow[] = AGENT_SHARE.flatMap(({ agentType, share }) =>
    Object.entries(AGENT_MODEL_MIX[agentType])
      .map(([model, weight]) => {
        const costUsd = Number((totalCostUsd * share * (weight ?? 0)).toFixed(2));
        // ~68% of spend is input tokens at this fixture's blended price point.
        const inputTokens = Math.round((costUsd * 0.68 * 1_000_000) / 3);
        const outputTokens = Math.round((costUsd * 0.32 * 1_000_000) / 15);
        return {
          agentType,
          model,
          inputTokens,
          outputTokens,
          costUsd,
          callCount: Math.max(1, Math.round(costUsd * 4)),
        };
      })
      // A zero-dollar row is noise in a table you scan for the expensive ones.
      .filter((r) => r.costUsd > 0),
  ).sort((a, b) => b.costUsd - a.costUsd);

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
