import { type NextRequest } from "next/server";

import { bffProxy } from "@/lib/bff/proxy";
import { Workspace } from "@/lib/schemas/workspace";

/**
 * One Business Unit — proxied to FastAPI `GET/PATCH /workspaces/{id}`.
 *
 * `/workspaces/:id` is directly guessable, so removing a sibling unit from the
 * list was only ever half the boundary. The backend closes the other half, and
 * closes it the same way: 404 rather than 403, because a 403 confirms the unit
 * exists.
 *
 * TWO WRITE RULES MOVE WITH IT, and both were real:
 *
 *   - `isActive` is org-wide only. FastAPI gates the whole PATCH on
 *     `workspace:manage`, which a unit's own Admin holds for their unit — so
 *     whether it separates deactivation from the rest is the backend's business
 *     now, and enforcing it here as well would only mask a gap if it doesn't.
 *   - The budget cascade (PRD §34.5): a unit's Admin may set the FIRST cap,
 *     because the Org Admin is allowed to create a unit without one and someone
 *     has to fill in the blank; changing a cap that already exists is a
 *     different act and belongs to the approval flow. That flow does not exist
 *     in the backend (see the budget-increase-request route), so this rule has
 *     no second half to route to and is not re-implemented here.
 *
 * BACKLOG: confirm FastAPI splits `isActive` and first-cap-vs-change on PATCH
 * /workspaces/{id}; both were enforced in this tier and neither is verified there.
 */
export async function GET(_req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return bffProxy(`/workspaces/${encodeURIComponent(id)}`, { schema: Workspace });
}

export async function PATCH(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const body: unknown = await req.json();
  return bffProxy(`/workspaces/${encodeURIComponent(id)}`, { method: "PATCH", body });
}
