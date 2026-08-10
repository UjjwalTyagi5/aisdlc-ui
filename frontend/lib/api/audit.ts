import { z } from "zod";

import { AuditEvent, paginated } from "@/lib/schemas";

import { api } from "./client";

export const listAuditEvents = (query?: {
  projectId?: string;
  actor?: string;
  action?: string;
  page?: number;
  pageSize?: number;
}) =>
  api("/audit", {
    query,
    schema: paginated(AuditEvent),
  });

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
