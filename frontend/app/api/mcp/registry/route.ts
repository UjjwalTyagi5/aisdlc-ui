import { type NextRequest } from "next/server";

import { bffProxy } from "@/lib/bff/proxy";

/**
 * MCP server registry — proxied to FastAPI `GET/POST /mcp/registry`.
 *
 * Previously a DUMMY-DATA SEAM over `lib/mock/mcp-fixtures`, which is why the
 * Integrations hub listed four servers ("Filesystem", "Postgres (staging)", …)
 * against an `mcp_servers` table holding none.
 *
 * `active_only` and `workspaceId` are forwarded rather than re-implemented — both
 * are the backend's own query parameters. Without `workspaceId` the backend
 * answers from row ownership (`created_by`) — the registry admin page's own
 * question. WITH one (the Tools-per-stage picker, at project creation and in
 * Settings — mirrors GET /connectors's identical param) it answers from grants
 * instead, regardless of who registered the server — see
 * shared/services/mcp_registry.py::list_servers.
 */
export const dynamic = "force-dynamic";

export async function GET(req: NextRequest) {
  const activeOnly = req.nextUrl.searchParams.get("active_only") === "true";
  const workspaceId = req.nextUrl.searchParams.get("workspaceId");
  const qs = new URLSearchParams();
  if (activeOnly) qs.set("active_only", "true");
  if (workspaceId) qs.set("workspaceId", workspaceId);
  const suffix = qs.toString();
  return bffProxy(`/mcp/registry${suffix ? `?${suffix}` : ""}`);
}

/**
 * Register a server. The org-admin check that used to live here is the
 * backend's now — `/mcp/registry` POST is gated on `connector:manage`, which is
 * the permission that actually means "may register one", rather than a role name
 * this tier matched on.
 */
export async function POST(req: NextRequest) {
  const body = (await req.json()) as { server_name?: string };
  if (!body?.server_name) {
    return Response.json({ code: "bad_request", message: "Name the server." }, { status: 400 });
  }
  return bffProxy("/mcp/registry", { method: "POST", body });
}
