import { z } from "zod";

import { Timestamp } from "@/lib/schemas/primitives";

import { api } from "./client";

/**
 * Schema matching CostBreakdownRow from agentic_app/shared/routers/_schemas.py
 * (REQ-M9-07/08/09) — one per-model aggregate row (grouped by model only).
 */
export const CostBreakdownRow = z.object({
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
  defaultProjectBudgetUsd: z.number().nonnegative(),
  generatedAt: Timestamp,
});
export type Budgets = z.infer<typeof Budgets>;

export const getBudgets = () => api("/cost/budgets", { schema: Budgets });

export const setBudget = (body: {
  scope: "org" | "workspace" | "project";
  id: string;
  monthlyBudgetUsd: number | null;
}) => api("/cost/budgets", { method: "PUT", body });
