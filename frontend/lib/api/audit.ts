import { z } from "zod";

import { AuditEvent, paginated } from "@/lib/schemas";

import { api } from "./client";

/**
 * A page of the audit trail, walked by CURSOR rather than by page number.
 *
 * `total` is still here — "of 4,312 matching" is what tells a reader whether their
 * filter did anything — but there is no page count, because with a cursor there is no
 * such thing as jumping to page 40. That is the trade: offset paging on a table that
 * only grows re-shows rows and skips others as new events arrive between clicks, and a
 * consistent walk is worth more on an audit trail than a deep-linkable page number.
 */
export const AuditPage = z.object({
  items: z.array(AuditEvent),
  nextCursor: z.string().nullish(),
  prevCursor: z.string().nullish(),
  total: z.number().int().nonnegative().default(0),
});
export type AuditPage = z.infer<typeof AuditPage>;

export const listAuditEvents = (query?: {
  projectId?: string;
  actor?: string;
  action?: string;
  /** Free-text. Matched SERVER-side — see shared/routers/audit.py::_search_clause. */
  q?: string;
  /** Opaque, from a previous response. Omit for the newest page. */
  cursor?: string;
  direction?: "next" | "prev";
  /** Which way the trail runs. Server-side — the page is one slice of it. */
  sort?: "newest" | "oldest";
  pageSize?: number;
}) =>
  api("/audit", {
    query,
    schema: AuditPage,
  });

/**
 * The whole filtered trail, produced BY THE SERVER — which is what makes the export
 * auditable. PRD §34.9 requires the act of exporting to be recorded, and a file the
 * browser assembles from rows it already holds can only be recorded by asking the
 * browser to own up. The same request that returns these rows writes that record.
 *
 * It also returns more than the page you are looking at: the old client-side export
 * serialised the current 50 rows into a file named after the audit log.
 */
export const exportAuditEvents = (query?: {
  fmt?: "csv" | "json";
  projectId?: string;
  workspaceId?: string;
  actor?: string;
  action?: string;
  q?: string;
}) => api("/audit/export", { query, schema: z.array(AuditEvent) });

/** Org-level audit — all workspaces (admin only). Optional workspace_id narrows scope. */
export const listOrgAuditEvents = (query?: {
  workspaceId?: string;
  actor?: string;
  action?: string;
  page?: number;
  pageSize?: number;
}) =>
  api("/admin/audit", {
    query,
    schema: paginated(AuditEvent),
  });

// ── M8: run-scoped audit trail (cursor-paginated) ──────────────────────────

/**
 * Schema matching the backend AuditEventOut from shared/routers/_schemas.py.
 * Distinct from the legacy AuditEvent (admin-audit-log shape) — this is the
 * per-run agentic event stream emitted by AuditCallbackHandler.
 */
export const RunAuditEvent = z.object({
  id: z.string().uuid(),
  tenant_id: z.string().uuid(),
  actor_id: z.string().nullable(),
  event_type: z.string(),
  resource_type: z.string().nullable(),
  resource_id: z.string().nullable(),
  payload: z.record(z.unknown()).nullable(),
  created_at: z.string().datetime({ offset: true }),
});
export type RunAuditEvent = z.infer<typeof RunAuditEvent>;

const CursorPage = z.object({
  items: z.array(RunAuditEvent),
  nextCursor: z.string().nullable(),
});
export type CursorPage = z.infer<typeof CursorPage>;

/** Fetch cursor-paginated audit events scoped to a single run. */
export const getRunAudit = (
  runId: string,
  query?: {
    cursor?: string;
    agent?: string;
    event_type?: string;
    actor?: string;
    since?: string;
    until?: string;
  },
) =>
  api(`/runs/${encodeURIComponent(runId)}/audit`, {
    query,
    schema: CursorPage,
  });
