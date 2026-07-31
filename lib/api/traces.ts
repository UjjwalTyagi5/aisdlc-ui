import { z } from "zod";

import { ProjectCostSummary, Trace, TraceListItem, TraceMetrics } from "@/lib/schemas";

import { api } from "./client";

export interface TraceFilters {
  agent?: string;
  project?: string;
  status?: string;
}

export const listTraces = (filters: TraceFilters = {}) =>
  api("/traces", {
    query: {
      agent: filters.agent || undefined,
      project: filters.project || undefined,
      status: filters.status || undefined,
    },
    schema: z.array(TraceListItem),
  });

export const getTraceMetrics = (windowDays?: number, filters: TraceFilters = {}) =>
  api("/traces/metrics", {
    query: {
      window_days: windowDays,
      agent: filters.agent || undefined,
      project: filters.project || undefined,
      status: filters.status || undefined,
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
