import { bffProxy } from "@/lib/bff/proxy";

import { OrgOverview } from "@/lib/schemas/org-overview";

/**
 * Organization dashboard rollup — proxied to FastAPI `GET /org/overview`.
 *
 * The scope filtering that used to live here is gone, and deliberately so. It was
 * computing counts from the caller's allowed set in this tier, which is the right
 * idea implemented in the wrong place: the backend holds the bindings, so it does
 * the narrowing there and this handler carries no access logic at all.
 *
 * `dynamic = "force-dynamic"` stays. The response depends on who is asking, and a
 * cached rollup would hand one unit's figures to another unit's admin.
 */
export const dynamic = "force-dynamic";

export async function GET() {
  return bffProxy("/org/overview", { schema: OrgOverview });
}
