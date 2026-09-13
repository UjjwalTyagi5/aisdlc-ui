import { type NextRequest } from "next/server";

import { ApiRequestError } from "@/lib/api/client";
import { getSession } from "@/lib/auth/session";
import { bffFetch } from "@/lib/bff/client";
import { bffProxy } from "@/lib/bff/proxy";
import { toCustomRole, type BackendCustomRole } from "../route";

/**
 * One custom role.
 *
 * DELETE is proxied to FastAPI `DELETE /admin/custom-roles/{id}`.
 *
 * PATCH is proxied to `PATCH /admin/custom-roles/{id}`. The ownership check that
 * used to guard both verbs here is the backend's, and its reasoning is unchanged: a
 * role belongs to the unit that defined it, and a Business Unit Admin editing
 * another unit's role — or the org-wide one every unit assigns — would change what
 * people outside their authority are allowed to do. So an org-scoped role needs
 * org-wide authority and a unit-scoped one needs write access to that unit.
 *
 * Permissions are replaced wholesale rather than merged: the request states the
 * complete set, so a delta would only add a way for the stored set to end up as
 * neither the old one nor the new one.
 *
 * IT TRANSLATES THE RESPONSE, like the collection route does. PATCH was a bare
 * `bffProxy`, which returns FastAPI's body untouched: `scopeKind` and `scopeId`
 * where the client's schema requires `scope` and `businessUnitId`. So every edit
 * wrote successfully and then threw "response did not match schema" on the way
 * back, which reads to the user as the save having failed. Two routes onto one
 * resource have to spell it the same way.
 */
export async function PATCH(req: NextRequest, ctx: { params: Promise<{ id: string }> }) {
  const { id } = await ctx.params;
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });

  const body: unknown = await req.json();
  try {
    const updated = (await bffFetch(`/admin/custom-roles/${encodeURIComponent(id)}`, {
      session,
      method: "PATCH",
      body,
    })) as BackendCustomRole;
    return Response.json(toCustomRole(updated));
  } catch (err) {
    if (err instanceof ApiRequestError) {
      return Response.json(
        err.details ?? { code: err.code, message: err.message },
        { status: err.status },
      );
    }
    throw err;
  }
}

export async function DELETE(_req: NextRequest, ctx: { params: Promise<{ id: string }> }) {
  const { id } = await ctx.params;
  return bffProxy(`/admin/custom-roles/${encodeURIComponent(id)}`, { method: "DELETE" });
}
