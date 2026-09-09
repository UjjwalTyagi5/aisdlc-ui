import { z } from "zod";

import { ProjectId, RunId, SpanId, TraceId } from "./ids";
import { AgentType, Status } from "./enums";
import { Cost, Timestamp } from "./primitives";

/** Span node kinds — mirrors Langfuse observation types. */
export const SpanType = z.enum(["generation", "tool", "retrieval", "span", "event"]);
export type SpanType = z.infer<typeof SpanType>;

/** Severity attached to a span (Langfuse `level`). */
export const SpanLevel = z.enum(["debug", "default", "warning", "error"]);
export type SpanLevel = z.infer<typeof SpanLevel>;

/** One eval/quality score attached to a trace (Langfuse score). */
export const TraceScore = z.object({
  name: z.string(),
  value: z.number(),
  comment: z.string().nullish(),
});
export type TraceScore = z.infer<typeof TraceScore>;

/** A node in the trace tree. parentId null ⇒ root. */
export const Span = z.object({
  id: SpanId,
  traceId: TraceId,
  parentId: SpanId.nullable(),
  name: z.string(),
  type: SpanType,
  level: SpanLevel,
  startedAt: Timestamp,
  /** Offset from trace start in ms — drives the waterfall left edge. */
  startOffsetMs: z.number().int().nonnegative(),
  latencyMs: z.number().int().nonnegative(),
  status: Status,
  statusMessage: z.string().nullish(),
  cost: Cost.nullish(),
  /** Short preview only — full I/O is opened on demand (and lives in Langfuse). */
  inputPreview: z.string().nullish(),
  outputPreview: z.string().nullish(),
});
export type Span = z.infer<typeof Span>;

/** Row in the traces table (list projection — no spans). */
export const TraceListItem = z.object({
  id: TraceId,
  runId: RunId.nullish(),
  projectId: ProjectId,
  projectName: z.string(),
  name: z.string(),
  agentType: AgentType,
  /** The member whose turn produced this trace — the Member filter runs on it. */
  userId: z.string().nullish(),
  /**
   * Null on list rows. Langfuse's trace-list response carries observation *ids*,
   * not observations, so the backend has no span levels to fold into an outcome
   * and does not guess one. The detail endpoint fills it in.
   */
  status: Status.nullish(),
  startedAt: Timestamp,
  latencyMs: z.number().int().nonnegative(),
  cost: Cost,
  model: z.string(),
  spanCount: z.number().int().nonnegative(),
  environment: z.string(),
  /** Worst span level present. Null on list rows — see `status`. */
  worstLevel: SpanLevel.nullish(),
  scores: z.array(TraceScore),
});
export type TraceListItem = z.infer<typeof TraceListItem>;

/** Full trace detail — list item + spans + a deep-link to the Langfuse trace. */
export const Trace = TraceListItem.extend({
  spans: z.array(Span),
  langfuseUrl: z.string().nullish(),
  release: z.string().nullish(),
});
export type Trace = z.infer<typeof Trace>;

/** Per-agent aggregate row inside the metrics envelope. */
export const TraceMetricsByAgent = z.object({
  agentType: AgentType,
  traceCount: z.number().int().nonnegative(),
  /** Null when unknown — see TraceMetrics.errorRate. */
  errorRate: z.number().min(0).max(1).nullish(),
  latencyP50Ms: z.number().int().nonnegative(),
  latencyP95Ms: z.number().int().nonnegative(),
  costUsd: z.number().nonnegative(),
});
export type TraceMetricsByAgent = z.infer<typeof TraceMetricsByAgent>;

/** Windowed metrics envelope for the page header strip. */
export const TraceMetrics = z.object({
  windowDays: z.number().int().positive(),
  totalTraces: z.number().int().nonnegative(),
  /**
   * Null when the backend cannot compute it, which is the list-based aggregate's
   * normal state — it has no span levels. The strip hides the tile rather than
   * showing a 0% that reads as a measured all-clear.
   */
  errorRate: z.number().min(0).max(1).nullish(),
  latencyP50Ms: z.number().int().nonnegative(),
  latencyP95Ms: z.number().int().nonnegative(),
  totalCostUsd: z.number().nonnegative(),
  byAgent: z.array(TraceMetricsByAgent),
  generatedAt: Timestamp,
});
export type TraceMetrics = z.infer<typeof TraceMetrics>;

/** Project-scoped LLM cost + token totals over a window (Langfuse-sourced). */
export const ProjectCostSummary = z.object({
  projectId: z.string(),
  windowDays: z.number().int().positive(),
  totalCostUsd: z.number().nonnegative(),
  inputTokens: z.number().int().nonnegative(),
  outputTokens: z.number().int().nonnegative(),
  totalTokens: z.number().int().nonnegative(),
  generatedAt: Timestamp,
});
export type ProjectCostSummary = z.infer<typeof ProjectCostSummary>;
