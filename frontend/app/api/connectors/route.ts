import { type NextRequest } from "next/server";

import { bffProxy } from "@/lib/bff/proxy";

/**
 * Connector integrations — proxied to FastAPI `GET /connectors`.
 *
 * Workspace scoping travels two ways, same as `/api/model/availability`: the
 * `?workspaceId=` query string is forwarded verbatim to FastAPI, which prefers it
 * over the `X-Workspace-Id` header `bffFetch` adds from the `sdlc_active_workspace`
 * cookie. Most callers (the Integrations hub) still rely on the header/cookie and
 * need not pass the param; a caller asking about a Business Unit other than the
 * ambient active workspace — e.g. the create-project dialog, scoping the tools
 * picker to whichever unit is selected in that dialog rather than the workspace
 * switcher elsewhere in the chrome — passes it explicitly.
 *
 * The backend returns connectors with credentials but no workspace_connectors row as
 * `disconnected` rather than hiding them — "installed for the org, not enabled here"
 * is a state the integrations hub needs to show, not an absence.
 *
 * force-dynamic: the response varies by workspace (cookie or query param), and
 * Next's Full Route Cache would otherwise persist the first response to disk and
 * serve one unit's connector list to another.
 */
export const dynamic = "force-dynamic";

export async function GET(req: NextRequest) {
  const qs = req.nextUrl.searchParams.toString();
  return bffProxy(`/connectors${qs ? `?${qs}` : ""}`);
}
