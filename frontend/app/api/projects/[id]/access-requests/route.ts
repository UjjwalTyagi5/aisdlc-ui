import { type NextRequest } from "next/server";

import { bffProxy } from "@/lib/bff/proxy";

/**
 * Borrow a contributor from another Business Unit for one project
 * ([[cross-bu-contributor-loan]]) — proxied to FastAPI
 * `POST /projects/{id}/access-requests`.
 *
 * Files a `cross_bu_assignment` governance request, routed to the
 * contributor's OWN business unit admin (the server derives their parent
 * unit from their email — never the workspaceId the asker happens to be
 * standing in). Approving it is a separate, still-unbuilt step: there is no
 * cross-BU grant table yet (see app/api/admin/cross-bu-grants/route.ts), so
 * `decide()` on this request type currently refuses with a clear
 * "not implemented" error rather than a silent no-op.
 */
export const dynamic = "force-dynamic";

export async function POST(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const body = (await req.json()) as { email?: string; roleName?: string; reason?: string };
  if (!body?.email || !body?.roleName) {
    return Response.json(
      { code: "invalid_input", message: "email and roleName are required" },
      { status: 422 },
    );
  }
  return bffProxy(`/projects/${encodeURIComponent(id)}/access-requests`, { method: "POST", body });
}
