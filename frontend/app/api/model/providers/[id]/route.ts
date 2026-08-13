import { type NextRequest } from "next/server";

import { bffProxy } from "@/lib/bff/proxy";

/**
 * One model provider — proxied to FastAPI `PATCH/DELETE /model/providers/{id}`.
 *
 * Previously mutated the shared fixture PROVIDERS array. The `model_providers`
 * table is where a provider actually lives, and it is what the sibling index
 * route (already a proxy) has been reading all along — so an edit made here used
 * to vanish the moment the list refetched.
 *
 * The 404-on-unknown-id that this handler produced is the backend's now, and its
 * version is the meaningful one: it 404s on another tenant's provider too, which
 * a fixture lookup by id could not distinguish.
 */
export async function PATCH(req: NextRequest, ctx: { params: Promise<{ id: string }> }) {
  const { id } = await ctx.params;
  const body: unknown = await req.json();
  return bffProxy(`/model/providers/${encodeURIComponent(id)}`, { method: "PATCH", body });
}

export async function DELETE(_req: NextRequest, ctx: { params: Promise<{ id: string }> }) {
  const { id } = await ctx.params;
  return bffProxy(`/model/providers/${encodeURIComponent(id)}`, { method: "DELETE" });
}
