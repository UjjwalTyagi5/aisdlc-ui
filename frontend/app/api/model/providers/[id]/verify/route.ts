import { type NextRequest } from "next/server";

import { bffProxy } from "@/lib/bff/proxy";

/**
 * Verify a provider's credential — proxied to FastAPI
 * `POST /model/providers/{id}/verify`.
 *
 * The fixture version flipped a status field and reported success without ever
 * calling the provider, which is the one thing a verify button must not do: an
 * unreachable endpoint or a revoked key came back green.
 */
export async function POST(_req: NextRequest, ctx: { params: Promise<{ id: string }> }) {
  const { id } = await ctx.params;
  return bffProxy(`/model/providers/${encodeURIComponent(id)}/verify`, { method: "POST" });
}
