import { type NextRequest } from "next/server";

import { bffProxy } from "@/lib/bff/proxy";

/**
 * One project's access to the integrations its unit was granted — proxied to
 * FastAPI `/projects/{id}/integrations/access`.
 *
 * THE THIRD RUNG. `/api/integrations/access` covers the middle one: whether a
 * Business Unit may use a thing at all, which is the Organization Admin's decision.
 * This covers the one below: whether a particular project gets the whole of that
 * grant or a narrower slice.
 *
 * Both admin tiers may set it — a Business Unit Admin because deciding what each of
 * their projects may do is what running a unit means, a Project Admin because
 * tightening your own project needs no permission from above. Neither may exceed the
 * unit's grant, and the server refuses rather than silently narrowing, so a caller
 * who asked for write is told they did not get it instead of believing they did.
 *
 * NO CHECKS HERE. The BFF forwards; FastAPI decides. Re-implementing the ceiling in
 * this tier would give it somewhere to drift from the rule that actually enforces.
 */
export const dynamic = "force-dynamic";

export async function GET(_req: NextRequest, ctx: { params: Promise<{ id: string }> }) {
  const { id } = await ctx.params;
  return bffProxy(`/projects/${encodeURIComponent(id)}/integrations/access`);
}

export async function PUT(req: NextRequest, ctx: { params: Promise<{ id: string }> }) {
  const { id } = await ctx.params;
  const body = await req.json().catch(() => null);
  return bffProxy(`/projects/${encodeURIComponent(id)}/integrations/access`, {
    method: "PUT",
    body,
  });
}

export async function DELETE(req: NextRequest, ctx: { params: Promise<{ id: string }> }) {
  const { id } = await ctx.params;
  const qs = req.nextUrl.searchParams.toString();
  return bffProxy(
    `/projects/${encodeURIComponent(id)}/integrations/access${qs ? `?${qs}` : ""}`,
    { method: "DELETE" },
  );
}
