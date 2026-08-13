import { type NextRequest } from "next/server";

import { bffProxy } from "@/lib/bff/proxy";

/**
 * MCP server registry — proxied to FastAPI `GET/POST /mcp/registry`.
 *
 * Previously a DUMMY-DATA SEAM over `lib/mock/mcp-fixtures`, which is why the
 * Integrations hub listed four servers ("Filesystem", "Postgres (staging)", …)
 * against an `mcp_servers` table holding none.
 *
 * The scope filter that ran here is gone with the fixtures. It narrowed the
 * fixture registry to the viewer's Business Units; the backend answers the same
 * question from its own row ownership (`created_by`), and a second filter in
 * this tier could only ever disagree with it.
 *
 * `active_only` is forwarded rather than re-implemented — it is the backend's
 * query parameter, spelled the same way.
 */
export const dynamic = "force-dynamic";

export async function GET(req: NextRequest) {
  const activeOnly = req.nextUrl.searchParams.get("active_only") === "true";
  return bffProxy(`/mcp/registry${activeOnly ? "?active_only=true" : ""}`);
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
