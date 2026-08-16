import { type NextRequest } from "next/server";

import { bffProxy } from "@/lib/bff/proxy";
import { WorkspaceList } from "@/lib/schemas/workspace";
import { BUSINESS_UNIT_LABEL } from "@/lib/scope";

/**
 * Business Units list + create — proxied to FastAPI `GET/POST /workspaces`.
 *
 * The scope filter that used to run here is now the backend's: its list query
 * returns every unit to an org admin and only bound units to everyone else, from
 * real role_bindings instead of a fixture array.
 *
 * The org-admin check on create moved to the backend too, and that was the point
 * of moving it. `workspace:manage` is held by a unit's own Admin for the unit they
 * run, so the router-level permission alone would have let them create siblings;
 * FastAPI now rejects that itself rather than trusting this tier to have done it.
 */
export async function GET() {
  return bffProxy("/workspaces", { schema: WorkspaceList });
}

export async function POST(req: NextRequest) {
  const body = (await req.json()) as { displayName?: string };
  // Kept as a fast local reject for the empty-form case only. It is not the
  // authorization check — that is the backend's, and duplicating one here is how
  // the two drift apart.
  if (!body?.displayName || body.displayName.trim().length < 2) {
    return Response.json(
      {
        code: "invalid_input",
        message: `${BUSINESS_UNIT_LABEL} name must be at least 2 characters`,
      },
      { status: 422 },
    );
  }
  return bffProxy("/workspaces", {
    method: "POST",
    body: { ...body, displayName: body.displayName.trim() },
  });
}
