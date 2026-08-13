import { type NextRequest } from "next/server";

import { bffProxy } from "@/lib/bff/proxy";

/**
 * Version history for one agent at one tier — proxied to FastAPI
 * `GET /agent-profiles/versions`.
 */
export const dynamic = "force-dynamic";

export async function GET(req: NextRequest) {
  const sp = new URLSearchParams(req.nextUrl.searchParams);
  if (!sp.get("agent_id")) {
    return Response.json(
      { code: "invalid_input", message: "agent_id is required" },
      { status: 422 },
    );
  }
  if (!sp.get("scope")) sp.set("scope", "workspace");
  return bffProxy(`/agent-profiles/versions?${sp.toString()}`);
}
