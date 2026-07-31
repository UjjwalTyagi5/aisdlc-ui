import { type NextRequest } from "next/server";

import { bffFetch } from "@/lib/bff/client";
import { getSession } from "@/lib/auth/session";

/**
 * POST /api/connectors/[kind]/install
 *
 * Wave C (REQ-M7-22): FastAPI now returns `{redirect_url: string}` for OAuth
 * connectors (snake_case). Normalize to camelCase `{redirectUrl}` before
 * forwarding to the client so the ConnectorInstallResponse union schema
 * (lib/schemas/connector.ts) can parse either shape uniformly.
 *
 * Non-OAuth / direct-install path: FastAPI may still return a Connector object
 * (e.g. ADO via PAT). In that case the raw data is passed through as-is.
 */
export async function POST(
  _req: NextRequest,
  { params }: { params: Promise<{ kind: string }> },
) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });

  const { kind } = await params;
  const data = await bffFetch(`/connectors/${encodeURIComponent(kind)}/install`, {
    session,
    method: "POST",
  });

  // Normalise FastAPI's snake_case redirect_url → camelCase redirectUrl so
  // the client-side ConnectorInstallResponse union can parse both shapes.
  if (
    data !== null &&
    typeof data === "object" &&
    "redirect_url" in data &&
    typeof (data as Record<string, unknown>)["redirect_url"] === "string"
  ) {
    const { redirect_url, ...rest } = data as Record<string, unknown>;
    return Response.json({ ...rest, redirectUrl: redirect_url });
  }

  return Response.json(data);
}
