import { bffProxy } from "@/lib/bff/proxy";

/**
 * The Business Unit picker on Roles & Access — proxied to FastAPI
 * `GET /admin/workspaces`.
 *
 * This route was ORIGINALLY a proxy and was converted to a fixture read when the
 * frontend ran standalone; the seam comment recorded that history. With FastAPI
 * up, it goes back to what it was.
 *
 * The `canManageBusinessUnit` filter is not reinstated on top. The endpoint is
 * gated on `member:manage` and its query is tenant-scoped, so the backend already
 * answers "which units may this caller act in"; a second filter in this tier over
 * a fixture store was how the two came to disagree in the first place.
 */
export const dynamic = "force-dynamic";

export async function GET() {
  return bffProxy("/admin/workspaces");
}
