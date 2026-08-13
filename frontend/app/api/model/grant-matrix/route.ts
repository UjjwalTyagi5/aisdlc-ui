import { bffProxy } from "@/lib/bff/proxy";

/**
 * Every model against every Business Unit — proxied to FastAPI
 * `GET /model/grant-matrix`.
 *
 * Org-admin only, and not merely because the page is theirs: the matrix names
 * every unit's standing against every model — who has what, and who has quietly
 * keyed something themselves. That is the whole organization's posture in one
 * payload, and it belongs to the tier that sets it. The backend now enforces that
 * rather than trusting this handler to have done it.
 *
 * One payload rather than N+1 calls because all four facts have to be read
 * together to answer the only question the grants screen exists for: "if I tick
 * this, can they use it?" A granted model with no key is visible and inert.
 */
export const dynamic = "force-dynamic";

export async function GET() {
  return bffProxy("/model/grant-matrix");
}
