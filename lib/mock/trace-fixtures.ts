/**
 * Dummy traces/spans/metrics for the Observability UI — plain data + pure
 * functions, server-safe (imported by the app/api/traces route handlers).
 *
 * Deterministic by construction (no Date.now at module scope) so the demo is
 * stable and the schema-validation test can't flake. This is the DUMMY-DATA
 * source; the Langfuse backend (Phase 2) replaces the route-handler bodies, not
 * these shapes.
 */
import type {
  AgentType,
  Span,
  SpanLevel,
  SpanType,
  Trace,
  TraceListItem,
  TraceMetrics,
  TraceScore,
} from "@/lib/schemas";

const BASE_MS = Date.UTC(2026, 5, 17, 12, 0, 0);

function iso(minutesAgo: number): string {
  return new Date(BASE_MS - minutesAgo * 60_000).toISOString();
}

const AGENTS: AgentType[] = ["requirements", "design", "development", "review", "testing"];

/**
 * Every ACTIVE seeded project (mocks/fixtures.ts), not just the first three.
 *
 * Traces are scope-filtered by project (lib/mock/access-scope.ts), and a project
 * with no traces is indistinguishable from a filter that dropped everything: the
 * Business Unit Admin of Platform Engineering saw an empty Traces tab purely
 * because `recon-bots` was missing from this list, which reads as a broken page
 * rather than as a boundary. Kept as a local literal rather than importing
 * PROJECTS so this module stays free of the project fixture graph.
 */
const PROJECTS = [
  { id: "mobile-onboarding", name: "Mobile onboarding journey" },
  { id: "payments-api", name: "Payments API — SCA exemption defect" },
  { id: "core-ledger", name: "Core ledger — Java 8 to 21" },
  { id: "recon-bots", name: "Reconciliation bots — A360 to UiPath" },
  { id: "fraud-features", name: "Fraud feature store pipeline" },
] as const;

function span(
  traceId: string,
  i: number,
  parentId: string | null,
  type: SpanType,
  name: string,
  startOffsetMs: number,
  latencyMs: number,
  opts: Partial<Span> = {},
): Span {
  const level: SpanLevel = opts.level ?? "default";
  return {
    id: `${traceId}-sp${i}` as Span["id"],
    traceId: traceId as Span["traceId"],
    parentId: (parentId as Span["parentId"]) ?? null,
    name,
    type,
    level,
    startedAt: iso(0),
    startOffsetMs,
    latencyMs,
    status: level === "error" ? "failed" : "approved",
    statusMessage: opts.statusMessage ?? null,
    model: type === "generation" ? { provider: "anthropic", id: "claude-sonnet-4-6" } : null,
    cost:
      type === "generation"
        ? { usd: 0.012 * (i + 1), inputTokens: 1800 + i * 400, outputTokens: 600 + i * 120 }
        : null,
    inputPreview: opts.inputPreview ?? null,
    outputPreview: opts.outputPreview ?? null,
  };
}

/** A realistic span tree: root → plan → llm → tool → llm → write. */
export function buildSpans(traceId: string, withError = false): Span[] {
  const root = span(traceId, 0, null, "span", "agent.run", 0, 8400, {
    inputPreview: "Goal: draft user stories for ING-214",
  });
  const plan = span(traceId, 1, root.id, "span", "plan", 40, 220);
  const gen1 = span(traceId, 2, plan.id, "generation", "llm.plan_call", 280, 1900, {
    inputPreview: "System: You are the Requirements agent…",
    outputPreview: "I will fetch the board item, then draft 3 stories…",
  });
  const tool = span(traceId, 3, root.id, "tool", "ado.fetch_item_detail", 2200, 640, {
    inputPreview: '{"itemId": 214}',
    outputPreview: '{"title": "Replay failed batch", "state": "Active"}',
  });
  const gen2 = span(traceId, 4, root.id, "generation", "llm.draft_stories", 2900, 4200, {
    inputPreview: "Draft acceptance criteria for: Replay failed batch",
    outputPreview: "Given a failed batch… When the engineer clicks Replay…",
    ...(withError
      ? {
          level: "error" as SpanLevel,
          statusMessage: "rate_limit: 429 from provider, retried 2×",
        }
      : {}),
  });
  const write = span(traceId, 5, root.id, "event", "artifact.write", 7300, 480, {
    inputPreview: "story[]",
    outputPreview: "wrote 3 artifacts",
  });
  return [root, plan, gen1, tool, gen2, write];
}

function scores(seed: number): TraceScore[] {
  return [
    { name: "faithfulness", value: Number((0.82 + (seed % 5) * 0.03).toFixed(2)), comment: null },
    { name: "helpfulness", value: Number((0.78 + (seed % 4) * 0.04).toFixed(2)), comment: null },
  ];
}

// Demo set: 6 traces — mostly approved with a couple of failures for contrast.
export const TRACES: Trace[] = Array.from({ length: 6 }).map((_, i) => {
  const traceId = `tr_${4200 + i}`;
  const project = PROJECTS[i % PROJECTS.length]!;
  const agent = AGENTS[i % AGENTS.length]!;
  const withError = i === 2 || i === 5;
  const spans = buildSpans(traceId, withError);
  const cost = spans.reduce(
    (acc, s) => ({
      usd: acc.usd + (s.cost?.usd ?? 0),
      inputTokens: acc.inputTokens + (s.cost?.inputTokens ?? 0),
      outputTokens: acc.outputTokens + (s.cost?.outputTokens ?? 0),
    }),
    { usd: 0, inputTokens: 0, outputTokens: 0 },
  );
  const latencyMs = Math.max(...spans.map((s) => s.startOffsetMs + s.latencyMs));
  return {
    id: traceId as Trace["id"],
    runId: `run_${2140 + i}` as Trace["runId"],
    projectId: project.id as Trace["projectId"],
    projectName: project.name,
    name: `${agent}.run`,
    agentType: agent,
    status: withError ? "failed" : "approved",
    startedAt: iso(i * 17),
    latencyMs,
    cost: {
      usd: Number(cost.usd.toFixed(4)),
      inputTokens: cost.inputTokens,
      outputTokens: cost.outputTokens,
    },
    model: "claude-sonnet-4-6",
    spanCount: spans.length,
    environment: i % 4 === 0 ? "staging" : "production",
    worstLevel: withError ? "error" : "default",
    scores: scores(i),
    spans,
    langfuseUrl: `https://cloud.langfuse.com/trace/${traceId}`,
    release: "v1.2.0",
    userId: i % 3 === 0 ? "agent" : "u_admin",
  };
});

/** Strip detail-only fields → the list projection. */
export function listItem(t: Trace): TraceListItem {
  const { spans: _spans, langfuseUrl: _url, release: _rel, userId: _uid, ...rest } = t;
  return rest;
}

export function percentile(values: number[], p: number): number {
  if (values.length === 0) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  const idx = Math.min(sorted.length - 1, Math.floor((p / 100) * sorted.length));
  return sorted[idx]!;
}

/**
 * @param traces the trace set to summarise — defaults to every trace, but the
 *   route passes the viewer's scope-filtered subset so the metrics strip agrees
 *   with the list beneath it. A total, error rate or p95 computed over traces
 *   the viewer cannot open is both a leak and a contradiction on screen.
 */
export function buildMetrics(windowDays: number, traces: Trace[] = TRACES): TraceMetrics {
  const byAgentMap = new Map<AgentType, Trace[]>();
  for (const t of traces) {
    const arr = byAgentMap.get(t.agentType) ?? [];
    arr.push(t);
    byAgentMap.set(t.agentType, arr);
  }
  const all = traces;
  const errCount = all.filter((t) => t.status === "failed").length;
  return {
    windowDays,
    totalTraces: all.length,
    // Guard the divisor: a scoped viewer can legitimately have zero traces,
    // and NaN fails the TraceMetrics schema at the client boundary.
    errorRate: all.length === 0 ? 0 : Number((errCount / all.length).toFixed(3)),
    latencyP50Ms: percentile(
      all.map((t) => t.latencyMs),
      50,
    ),
    latencyP95Ms: percentile(
      all.map((t) => t.latencyMs),
      95,
    ),
    totalCostUsd: Number(all.reduce((a, t) => a + t.cost.usd, 0).toFixed(2)),
    byAgent: Array.from(byAgentMap.entries()).map(([agentType, ts]) => ({
      agentType,
      traceCount: ts.length,
      errorRate: Number((ts.filter((t) => t.status === "failed").length / ts.length).toFixed(3)),
      latencyP50Ms: percentile(
        ts.map((t) => t.latencyMs),
        50,
      ),
      latencyP95Ms: percentile(
        ts.map((t) => t.latencyMs),
        95,
      ),
      costUsd: Number(ts.reduce((a, t) => a + t.cost.usd, 0).toFixed(4)),
    })),
    generatedAt: iso(0),
  };
}

/** Trace for a given runId — powers the run-detail Trace tab. */
export function traceForRun(runId: string): Trace | undefined {
  return TRACES.find((t) => t.runId === runId);
}
