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
 * The side effect the mock's `assignBusinessUnitRole` had — closing any open
 * `role_assignment` request for this person — now lives in FastAPI's
 * `update_workspace_member_role` (`shared/routers/workspaces.py`, calling
 * `complete_role_assignment` in `shared/services/governance_requests.py`), so it
 * fires for every caller of this endpoint, not just this one screen.
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
