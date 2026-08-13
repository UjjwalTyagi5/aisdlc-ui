import { type NextRequest } from "next/server";

import { bffProxy } from "@/lib/bff/proxy";
import { notImplemented } from "@/lib/bff/not-implemented";

/**
 * One custom role.
 *
 * DELETE is proxied to FastAPI `DELETE /admin/custom-roles/{id}`.
 *
 * PATCH is not implemented by the backend — `/admin/custom-roles` offers create,
 * list and delete, and nothing that edits a role in place. The fixture version
 * edited one, so renaming a role or adding a permission appeared to work and
 * reverted on reload.
 *
 * The ownership check that guarded both verbs here is the backend's now. Its
 * reasoning is unchanged and worth keeping in view: a role belongs to the unit
 * that defined it, and a Business Unit Admin editing another unit's role — or the
 * org-wide one every unit assigns — would change what people outside their
 * authority are allowed to do.
 *
 * BACKLOG: FastAPI `PATCH /admin/custom-roles/{id}`.
 */
export async function PATCH() {
  return notImplemented("PATCH /admin/custom-roles/{id}");
}

export async function DELETE(_req: NextRequest, ctx: { params: Promise<{ id: string }> }) {
  const { id } = await ctx.params;
  return bffProxy(`/admin/custom-roles/${encodeURIComponent(id)}`, { method: "DELETE" });
}
