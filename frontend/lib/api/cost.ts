import { z } from "zod";

import { SpendSeries, type SpendGroupBy } from "@/lib/schemas/spend-series";
import { Phase } from "@/lib/schemas/enums";

import { Timestamp } from "@/lib/schemas/primitives";

import { api } from "./client";

/**
 * Schema matching CostBreakdownRow from agentic_app/shared/routers/_schemas.py
 * (REQ-M9-07/08/09) — one aggregate row per model, optionally per (agent, model).
 *
 * `agentType` IS OPTIONAL, AND THAT IS NOT A STYLE CHOICE. The backend does not
 * emit it: CostBreakdownRow in _schemas.py carries model/tokens/cost/callCount
 * only, and tests/cost/test_cost_api.py asserts `"agentType" not in rows[0]`,
 * because /cost is sourced from Langfuse's daily-metrics endpoint, which
 * aggregates by model. Requiring the field here made this schema unparseable
 * against the real API — harmless only while ENABLE_LANGFUSE was false and
 * `rows` came back empty (an empty array parses fine), and a hard throw in
 * CostDashboard on the first real row.
 *
 * Agent-level attribution (PRD FR-09) is still the question worth answering —
 * "which agent is expensive" is what decides where to tune a prompt. Getting it
 * back means one Langfuse query per agent_type tag, merged the way cost.py
 * already fans out per workspace. Until that exists, the table hides the column
 * rather than inventing a value for it.
 */
export const CostBreakdownRow = z.object({
  /**
   * The agent that consumed it, as a pipeline phase (`PHASE_LABEL` renders it).
   * Absent whenever the row came from a model-only aggregate — see above.
   */
  agentType: Phase.optional(),
  model: z.string(),
  inputTokens: z.number().int().nonnegative(),
  outputTokens: z.number().int().nonnegative(),
  costUsd: z.number().nonnegative(),
  callCount: z.number().int().nonnegative(),
});
export type CostBreakdownRow = z.infer<typeof CostBreakdownRow>;

/**
 * Schema matching CostBreakdownOut from agentic_app/shared/routers/_schemas.py —
 * GET /cost response envelope, scoped to the requesting tenant (RLS +
 * defense-in-depth WHERE, T-9.2-11). budgetUsd/utilization/breached80 carry
 * the budget-breach signal (REQ-M9-09); alert delivery is deferred (DLT-9).
 */
export const CostBreakdown = z.object({
  windowDays: z.number().int().positive(),
  totalCostUsd: z.number().nonnegative(),
  totalInputTokens: z.number().int().nonnegative(),
  totalOutputTokens: z.number().int().nonnegative(),
  rows: z.array(CostBreakdownRow),
  generatedAt: Timestamp,
  budgetUsd: z.number().nonnegative(),
  utilization: z.number().nonnegative(),
  breached80: z.boolean(),
});
export type CostBreakdown = z.infer<typeof CostBreakdown>;

/**
 * Fetch the requesting tenant's per-agent/per-model LLM spend over a window.
 * Goes through the BFF route (/api/cost) — never calls FastAPI directly
 * (T-9.2-09: the browser never holds a FastAPI token).
 */
export const getCostBreakdown = (windowDays?: number, workspace?: string | null) =>
  api("/cost", {
    query: { window_days: windowDays, workspace: workspace || undefined },
    schema: CostBreakdown,
  });

// ── Budget hub (Cost page) ────────────────────────────────────────────────────

export const BudgetRow = z.object({
  scope: z.enum(["org", "workspace", "project"]),
  id: z.string(),
  name: z.string(),
  parentId: z.string().nullable().optional(),
  monthlyBudgetUsd: z.number().nonnegative().nullable().optional(),
  monthlySpendUsd: z.number().nonnegative(),
  allocatedUsd: z.number().nonnegative().nullable().optional(),
});
export type BudgetRow = z.infer<typeof BudgetRow>;

export const Budgets = z.object({
  org: BudgetRow,
  workspaces: z.array(BudgetRow),
  projects: z.array(BudgetRow),
  generatedAt: Timestamp,
});
export type Budgets = z.infer<typeof Budgets>;

export const getBudgets = () => api("/cost/budgets", { schema: Budgets });

export const setBudget = (body: {
  scope: "org" | "workspace" | "project";
  id: string;
  monthlyBudgetUsd: number | null;
}) => api("/cost/budgets", { method: "PUT", body });

/**
 * Monthly spend split by business unit, project or model — the dashboard
 * chart's data, refetched whenever a filter changes.
 *
 * `workspaceId` omitted (or "all") means every unit the caller may read, which
 * is why a Business Unit Admin can use this endpoint unchanged: the server
 * bounds "all" to their own scope.
 */
export const getSpendSeries = (params?: {
  groupBy?: SpendGroupBy;
  workspaceId?: string | null;
  months?: number;
  /** One project's own series — implies `groupBy: "project"` server-side. */
  projectId?: string | null;
}) =>
  api("/cost/spend-series", {
    query: {
      groupBy: params?.groupBy ?? "business_unit",
      workspaceId: params?.workspaceId ?? "all",
      months: params?.months ?? 6,
      projectId: params?.projectId || undefined,
    },
    schema: SpendSeries,
  });
