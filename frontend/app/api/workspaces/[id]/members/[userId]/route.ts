import { type NextRequest } from "next/server";

import { bffProxy } from "@/lib/bff/proxy";

type Params = Promise<{ id: string; userId: string }>;

/**
 * One person's role inside one Business Unit — proxied to FastAPI
 * `PATCH/DELETE /workspaces/{id}/members/{userId}`.
 *
 * The unit-admin guard is the backend's now. The read it protects is unchanged
 * and still deliberate: the people directory is org-wide, a Business Unit Admin
 * can see every colleague and the role each one holds, and this is the boundary
 * that keeps that a READ. Enforced in the API rather than by which buttons the
 * page renders — the page is not where that decision belongs.
 *
 * ONE SIDE EFFECT IS LOST. `assignBusinessUnitRole` also closed any open
 * `role_assignment` request for this person, so the obligation was discharged by
 * the write rather than by whichever screen the admin used. Governance requests
 * do not exist in the backend (see app/api/governance-approvals/route.ts), so
 * there is no request left to close — the queue that held it is empty.
 *
 * BACKLOG: re-attach the close-the-request side effect when governance requests
 * land, in FastAPI rather than here, so it fires for every caller.
 */
export async function PATCH(req: NextRequest, { params }: { params: Params }) {
  const { id, userId } = await params;
  const body = (await req.json().catch(() => ({}))) as { roleName?: string };
  if (!body.roleName) {
    return Response.json(
      { code: "validation_error", message: "roleName is required" },
      { status: 422 },
    );
  }
  return bffProxy(
    `/workspaces/${encodeURIComponent(id)}/members/${encodeURIComponent(userId)}`,
    { method: "PATCH", body },
  );
}

export async function DELETE(_req: NextRequest, { params }: { params: Params }) {
  const { id, userId } = await params;
  return bffProxy(
    `/workspaces/${encodeURIComponent(id)}/members/${encodeURIComponent(userId)}`,
    { method: "DELETE" },
  );
}
