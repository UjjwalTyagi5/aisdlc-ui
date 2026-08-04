/**
 * The Cost fixture's reconciliation invariants.
 *
 * The breakdown is a cross of two share tables (agent × model), and three
 * different surfaces read slices of it: the Cost page's meter reads the total,
 * its table reads the rows, and the dashboard's spend chart reads the per-model
 * roll-up. Nothing in the type system stops those from disagreeing — the bug
 * that prompted these tests was exactly that, an agent mix that put Opus at 30%
 * of spend beside a hand-written table that still said 23%, with the headline
 * total correct in both so neither looked wrong on its own.
 *
 * These assert the arithmetic that keeps every lens a view of one number.
 */
import { describe, it, expect } from "vitest";

import { buildCostBreakdown } from "@/lib/mock/cost-fixtures";

/** Cents, not floats — these are money figures rounded per row. */
const CENT = 0.011;

describe("cost breakdown reconciliation", () => {
  const data = buildCostBreakdown(30);

  it("produces a row per (agent, model) pair the agent actually runs on", () => {
    expect(data.rows.length).toBeGreaterThan(0);
    const keys = data.rows.map((r) => `${r.agentType}::${r.model}`);
    expect(new Set(keys).size).toBe(keys.length);
  });

  it("sums its rows to the headline total", () => {
    const summed = data.rows.reduce((a, r) => a + r.costUsd, 0);
    // One row's rounding can move the sum by half a cent; the fixture has
    // ~16 rows, so allow a cent per row rather than an exact match.
    expect(Math.abs(summed - data.totalCostUsd)).toBeLessThan(CENT * data.rows.length);
  });

  it("carries no zero-dollar rows", () => {
    expect(data.rows.every((r) => r.costUsd > 0)).toBe(true);
  });

  it("orders rows by spend, heaviest first", () => {
    const costs = data.rows.map((r) => r.costUsd);
    expect([...costs].sort((a, b) => b - a)).toEqual(costs);
  });

  // The two roll-ups the UI actually renders. Either one drifting from the
  // total is the failure mode that shipped once already.
  it("reconciles when rolled up by agent", () => {
    const byAgent = new Map<string, number>();
    for (const r of data.rows) {
      byAgent.set(r.agentType, (byAgent.get(r.agentType) ?? 0) + r.costUsd);
    }
    const summed = [...byAgent.values()].reduce((a, b) => a + b, 0);
    expect(Math.abs(summed - data.totalCostUsd)).toBeLessThan(CENT * data.rows.length);
  });

  it("reconciles when rolled up by model", () => {
    const byModel = new Map<string, number>();
    for (const r of data.rows) {
      byModel.set(r.model, (byModel.get(r.model) ?? 0) + r.costUsd);
    }
    const summed = [...byModel.values()].reduce((a, b) => a + b, 0);
    expect(Math.abs(summed - data.totalCostUsd)).toBeLessThan(CENT * data.rows.length);
  });

  it("keeps token totals equal to the sum of their rows", () => {
    expect(data.rows.reduce((a, r) => a + r.inputTokens, 0)).toBe(data.totalInputTokens);
    expect(data.rows.reduce((a, r) => a + r.outputTokens, 0)).toBe(data.totalOutputTokens);
  });
});

describe("cost breakdown scope filtering", () => {
  it("narrows the total when the viewer may read only some units", () => {
    const all = buildCostBreakdown(30);
    const none = buildCostBreakdown(30, null, []);
    expect(none.totalCostUsd).toBe(0);
    expect(none.rows).toHaveLength(0);
    expect(all.totalCostUsd).toBeGreaterThan(0);
  });
});
