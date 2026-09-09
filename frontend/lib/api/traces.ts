import { z } from "zod";

import { ProjectCostSummary, Trace, TraceListItem, TraceMetrics } from "@/lib/schemas";

import { api } from "./client";

/**
 * Filters the backend actually applies.
 *
 * `status` used to be here and was never one of them: GET /traces and
 * /traces/metrics accepted the parameter and no code path read it, so the Status
 * dropdown narrowed nothing while looking like it did. On an evidence surface a
 * filter that silently does nothing is worse than no filter, so it is gone until
 * per-trace level data reaches the list endpoint.
 */
export interface TraceFilters {
  agent?: string;
  project?: string;
  /**
   * Platform user id. Sent as Langfuse's first-class `userId` query field, so a
   * member filter narrows the query itself and stays correctly paged — PRD 32.1
   * groups traces unit -> project -> member, and 15.9 gives the Security Engineer a
   * view grouped by member.
   */
  user?: string;
}

export const listTraces = (filters: TraceFilters = {}) =>
  api("/traces", {
    query: {
      agent: filters.agent || undefined,
      project: filters.project || undefined,
      user: filters.user || undefined,
    },
    schema: z.array(TraceListItem),
  });

export const getTraceMetrics = (windowDays?: number, filters: TraceFilters = {}) =>
  api("/traces/metrics", {
    query: {
      window_days: windowDays,
      agent: filters.agent || undefined,
      project: filters.project || undefined,
      user: filters.user || undefined,
    },
    schema: TraceMetrics,
  });

export const getTrace = (id: string) =>
  api(`/traces/${encodeURIComponent(id)}`, { schema: Trace });

export const getProjectCostSummary = (projectId: string, windowDays = 7) =>
  api("/traces/project-summary", {
    query: { project_id: projectId, window_days: windowDays },
    schema: ProjectCostSummary,
  });
