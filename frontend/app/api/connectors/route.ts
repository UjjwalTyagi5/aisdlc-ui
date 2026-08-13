import { bffProxy } from "@/lib/bff/proxy";

/**
 * Connector integrations — proxied to FastAPI `GET /connectors`.
 *
 * Workspace scoping travels as the `X-Workspace-Id` header, which `bffFetch` adds
 * from the `sdlc_active_workspace` cookie on every BFF call. The old `?workspaceId=`
 * query param is therefore no longer read: the backend scopes on the header alone,
 * and honouring a second, caller-supplied source of truth for "which unit am I in"
 * is how the two disagree.
 *
 * The backend returns connectors with credentials but no workspace_connectors row as
 * `disconnected` rather than hiding them — "installed for the org, not enabled here"
 * is a state the integrations hub needs to show, not an absence.
 *
 * force-dynamic: the response varies by the active-workspace cookie, and Next's Full
 * Route Cache would otherwise persist the first response to disk and serve one unit's
 * connector list to another.
 */
export const dynamic = "force-dynamic";

export async function GET() {
  return bffProxy("/connectors");
}
