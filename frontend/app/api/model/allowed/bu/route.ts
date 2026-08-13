import { type NextRequest } from "next/server";

import { bffProxy } from "@/lib/bff/proxy";

/**
 * What one Business Unit may use — proxied to FastAPI `GET/PUT /model/allowed/bu`.
 *
 * The GET is DERIVED from the org grants, so a BU Admin reading it sees exactly
 * what was given to them and nothing they chose themselves. The PUT is the Org
 * Admin's per-unit control from unit creation and management — still theirs, not
 * the unit's, and gated as such in the backend.
 */
export const dynamic = "force-dynamic";

function requireWorkspaceId(req: NextRequest): string | Response {
  const workspaceId = req.nextUrl.searchParams.get("workspaceId");
  if (!workspaceId) {
    return Response.json(
      { code: "invalid_input", message: "workspaceId is required" },
      { status: 422 },
    );
  }
  return workspaceId;
}

export async function GET(req: NextRequest) {
  const workspaceId = requireWorkspaceId(req);
  if (workspaceId instanceof Response) return workspaceId;
  return bffProxy(`/model/allowed/bu?workspaceId=${encodeURIComponent(workspaceId)}`);
}

export async function PUT(req: NextRequest) {
  const workspaceId = requireWorkspaceId(req);
  if (workspaceId instanceof Response) return workspaceId;
  const body = (await req.json()) as { entries?: unknown[] };
  return bffProxy(`/model/allowed/bu?workspaceId=${encodeURIComponent(workspaceId)}`, {
    method: "PUT",
    body: { entries: body.entries ?? [] },
  });
}
