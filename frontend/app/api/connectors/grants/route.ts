import { type NextRequest } from "next/server";

import { bffProxy } from "@/lib/bff/proxy";

/**
 * Connector grants — proxied to FastAPI `GET/PUT /connectors/grants`, over the
 * `integration_grants` table that did not exist when this route returned an empty
 * list.
 *
 * Kept as grants rather than bare kinds so the UI can tell "every unit has this" from
 * "you were given this". A bounded viewer gets the union across their own units with
 * the unit lists intact only for those — they should not learn which OTHER units a
 * grant reaches, which is why the narrowing happens in the backend rather than by
 * trimming a full policy here.
 */
export const dynamic = "force-dynamic";

export async function GET(req: NextRequest) {
  const workspaceId = req.nextUrl.searchParams.get("workspaceId");
  const qs = workspaceId ? `?workspaceId=${encodeURIComponent(workspaceId)}` : "";
  return bffProxy(`/connectors/grants${qs}`);
}

export async function PUT(req: NextRequest) {
  const workspaceId = req.nextUrl.searchParams.get("workspaceId");
  const qs = workspaceId ? `?workspaceId=${encodeURIComponent(workspaceId)}` : "";
  const body: unknown = await req.json();
  return bffProxy(`/connectors/grants${qs}`, { method: "PUT", body });
}
