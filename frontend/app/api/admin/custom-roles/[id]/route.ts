import { type NextRequest } from "next/server";

import { bffProxy } from "@/lib/bff/proxy";

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
 */
export async function PATCH(req: NextRequest, ctx: { params: Promise<{ id: string }> }) {
  const { id } = await ctx.params;
  const body: unknown = await req.json();
  return bffProxy(`/admin/custom-roles/${encodeURIComponent(id)}`, { method: "PATCH", body });
}

export async function DELETE(_req: NextRequest, ctx: { params: Promise<{ id: string }> }) {
  const { id } = await ctx.params;
  return bffProxy(`/admin/custom-roles/${encodeURIComponent(id)}`, { method: "DELETE" });
}
