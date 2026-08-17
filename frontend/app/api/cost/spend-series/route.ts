import { type NextRequest } from "next/server";

import { bffProxy } from "@/lib/bff/proxy";
import { SpendSeries } from "@/lib/schemas/spend-series";

/**
 * Monthly spend split by business unit, project, model or provider — proxied to
 * FastAPI `GET /cost/spend-series`.
 *
 * Every figure now comes from agent_call_logs joined through runs → projects →
 * workspaces, so an empty chart means no agent calls have been billed, not that a
 * fixture ran out of months.
 *
 * The scope rules moved to the backend but did not change: the caller's allowed
 * units bound the result before their own `workspaceId` choice narrows it, so
 * "all" can only ever mean "all of mine", and a unit they cannot read is refused
 * with a 404 rather than silently widened to org-wide totals.
 */
export const dynamic = "force-dynamic";

export async function GET(req: NextRequest) {
  const from = req.nextUrl.searchParams;
  const to = new URLSearchParams();
  to.set("groupBy", from.get("groupBy") ?? "business_unit");
  to.set("months", from.get("months") ?? "6");

  // "all" is the frontend's sentinel for unfiltered; the backend treats an absent
  // param the same way, so it is dropped rather than forwarded as a literal id.
  const workspaceId = from.get("workspaceId");
  if (workspaceId && workspaceId !== "all") to.set("workspaceId", workspaceId);
  const projectId = from.get("projectId");
  if (projectId) to.set("projectId", projectId);

  return bffProxy(`/cost/spend-series?${to.toString()}`, { schema: SpendSeries });
}
