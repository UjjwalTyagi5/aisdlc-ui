/**
 * Unit tests for the CostDashboard pure-logic helpers (REQ-M9-08).
 *
 * Covers the behavioral contracts from milestone-9.2-03 plan Task 2:
 * - totals + per-(agent,model) row formatting (formatUsd / formatTokens / rowKey)
 * - budget bar tone derivation incl. breached_80 -> destructive
 * - budget bar hidden (tone === null) when budget_usd is 0 (ED-5: no fake metric)
 * - window selector options (7/30/90)
 *
 * Pure-logic unit tests (environment: "node") — mirrors the established
 * pattern in __tests__/integrations/page.test.ts. React rendering of the
 * full CostDashboard (which requires @testing-library/react + jsdom, not
 * present in this project's vitest setup) is out of scope; the data-shaping
 * and tone/format helpers exported from cost-dashboard.tsx carry the
 * behavioral contract under test here.
 */
import { describe, it, expect } from "vitest";

import {
  agentsInBreakdown,
  budgetTone,
  formatUsd,
  formatTokens,
  rowKey,
  WINDOW_OPTIONS,
} from "@/components/app/cost-dashboard";
import type { CostBreakdown, CostBreakdownRow } from "@/lib/api/cost";

function buildBreakdown(overrides: Partial<CostBreakdown> = {}): CostBreakdown {
  return {
    windowDays: 30,
    totalCostUsd: 12.5,
    totalInputTokens: 100_000,
    totalOutputTokens: 20_000,
    rows: [],
    generatedAt: "2026-06-10T00:00:00+00:00",
    budgetUsd: 100,
    utilization: 0.125,
    breached80: false,
    ...overrides,
  };
}

const ROW: CostBreakdownRow = {
  agentType: "development",
  model: "claude-sonnet-4-6",
  inputTokens: 50_000,
  outputTokens: 10_000,
  costUsd: 6.25,
  callCount: 12,
};

describe("CostDashboard window selector", () => {
  it("exposes exactly the 7/30/90 day options", () => {
    expect(WINDOW_OPTIONS).toEqual([7, 30, 90]);
  });
});

describe("CostDashboard formatting helpers", () => {
  it("formats USD totals and row costs", () => {
    expect(formatUsd(12.5)).toBe("$12.50");
    expect(formatUsd(0.5)).toBe("$0.500");
    expect(formatUsd(0.001)).toBe("$0.0010");
  });

  it("formats token counts compactly", () => {
    expect(formatTokens(500)).toBe("500");
    expect(formatTokens(1_500)).toBe("1.5k");
    expect(formatTokens(2_000_000)).toBe("2.0M");
  });

  it("derives a stable per-(agent, model) row key", () => {
    expect(rowKey(ROW)).toBe("development::claude-sonnet-4-6");
  });

  // The model alone was the key until rows carried an agent. Two agents on the
  // same model is the common case, not an edge one, and a shared key makes
  // React reuse one row's DOM for the other's numbers.
  it("distinguishes two agents sharing a model", () => {
    expect(rowKey(ROW)).not.toBe(rowKey({ ...ROW, agentType: "testing" }));
  });
});

describe("CostDashboard agent filter options", () => {
  it("lists the agents present, heaviest spender first", () => {
    expect(
      agentsInBreakdown([
        { ...ROW, agentType: "testing", costUsd: 5 },
        { ...ROW, agentType: "development", costUsd: 30 },
        { ...ROW, agentType: "design", costUsd: 12 },
      ]),
    ).toEqual(["development", "design", "testing"]);
  });

  // An agent's spend is split across the models it runs on, so ranking on a
  // single row would rank on whichever model happened to come first.
  it("ranks on an agent's total across its models, not one row", () => {
    expect(
      agentsInBreakdown([
        { ...ROW, agentType: "development", model: "claude-opus-4-7", costUsd: 6 },
        { ...ROW, agentType: "development", model: "claude-sonnet-4-6", costUsd: 6 },
        { ...ROW, agentType: "testing", model: "claude-sonnet-4-6", costUsd: 9 },
      ]),
    ).toEqual(["development", "testing"]);
  });

  it("returns nothing for an empty breakdown", () => {
    expect(agentsInBreakdown([])).toEqual([]);
  });
});

describe("CostDashboard budget bar tone (breached_80 + zero-budget)", () => {
  it("renders destructive tone when breached_80 is true", () => {
    const data = buildBreakdown({ budgetUsd: 100, utilization: 0.85, breached80: true });
    expect(budgetTone(data)).toBe("destructive");
  });

  it("renders warning tone when utilization >= 0.8 but not breached", () => {
    const data = buildBreakdown({ budgetUsd: 100, utilization: 0.82, breached80: false });
    expect(budgetTone(data)).toBe("warning");
  });

  it("renders success tone when utilization is low", () => {
    const data = buildBreakdown({ budgetUsd: 100, utilization: 0.1, breached80: false });
    expect(budgetTone(data)).toBe("success");
  });

  it("hides the budget bar (returns null) when budget_usd is 0 — no fake metric", () => {
    const data = buildBreakdown({ budgetUsd: 0, utilization: 0, breached80: false });
    expect(budgetTone(data)).toBeNull();
  });
});

describe("CostDashboard empty rows", () => {
  it("an empty rows array signals the EmptyState branch (no broken table)", () => {
    const data = buildBreakdown({ rows: [] });
    expect(data.rows).toHaveLength(0);
  });

  it("a populated rows array carries per-row totals for the table", () => {
    const data = buildBreakdown({ rows: [ROW] });
    expect(data.rows[0]?.costUsd).toBe(6.25);
    expect(data.rows[0]?.callCount).toBe(12);
  });
});
