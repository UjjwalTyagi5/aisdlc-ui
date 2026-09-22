import { describe, expect, it } from "vitest";
import { CostBreakdown } from "@/lib/api/cost";

// The verbatim body of GET /cost from the live backend, 2026-09-15 — the one that
// took the Cost & Budget page down with SCHEMA_MISMATCH. `agentType` is an explicit
// null: the row came from a `LangGraph` trace, which carries no `sdlc:` prefix and so
// has no agent to attribute. `Phase.optional()` accepts undefined and rejects null,
// so the page threw on the first real row it was ever shown.
const REAL_BODY = {
  windowDays: 30,
  totalCostUsd: 0.009498,
  totalInputTokens: 42539,
  totalOutputTokens: 2276,
  rows: [
    {
      agentType: null,
      model: "azure/gpt-5-mini",
      inputTokens: 42539,
      outputTokens: 2276,
      costUsd: 0.009498,
      callCount: 7,
    },
  ],
  generatedAt: "2026-09-15T04:55:58.639271+00:00",
  budgetUsd: 0.0,
  utilization: 0.0,
  breached80: false,
  degraded: false,
  degradedProjects: 0,
};

describe("CostBreakdown against what the backend actually sends", () => {
  it("accepts the real body, agentType null and all", () => {
    const r = CostBreakdown.safeParse(REAL_BODY);
    expect(r.success, JSON.stringify(r.success ? {} : r.error.issues, null, 2)).toBe(true);
  });

  it("keeps the null rather than inventing an agent", () => {
    const r = CostBreakdown.parse(REAL_BODY);
    expect(r.rows[0]!.agentType).toBeNull();
  });

  it("still accepts a row that does attribute an agent", () => {
    const rows = [{ ...REAL_BODY.rows[0], agentType: "development" }];
    expect(CostBreakdown.parse({ ...REAL_BODY, rows }).rows[0]!.agentType).toBe("development");
  });

  it("accepts an omitted agentType, which is what the pre-dimensions backend sent", () => {
    const { agentType: _dropped, ...noAgent } = REAL_BODY.rows[0]!;
    expect(CostBreakdown.safeParse({ ...REAL_BODY, rows: [noAgent] }).success).toBe(true);
  });

  // The wire type is a free-form string sliced out of the trace name, while Phase is a
  // closed set. An agent this build has never heard of must read as unattributed, not
  // take the whole page down the way null did.
  it("degrades an unknown agent to null instead of failing the page", () => {
    const rows = [{ ...REAL_BODY.rows[0], agentType: "something-new" }];
    const r = CostBreakdown.safeParse({ ...REAL_BODY, rows });
    expect(r.success).toBe(true);
    expect(r.success && r.data.rows[0]!.agentType).toBeNull();
  });
});
