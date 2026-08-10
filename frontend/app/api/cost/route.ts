import { type NextRequest } from "next/server";

import { buildCostBreakdown } from "@/lib/mock/cost-fixtures";
import { getSession } from "@/lib/auth/session";
import { resolveSessionScope } from "@/lib/auth/access-scope";

// DUMMY-DATA SEAM: derives fixtures directly from the workspace/project
// fixtures. When the Langfuse-backed cost pipeline lands, replace the body
// with: return bffProxy(`/cost?${search}`).
//
// SCOPE FILTER: spend is commercially sensitive between units, and an
// unfiltered TOTAL leaks even without a per-unit breakdown — "the organisation
// spent $21,889 this month" tells a Business Unit Admin what the other two units
// cost. The viewer's readable units are intersected with whichever unit they
// asked for.
export async function GET(req: NextRequest) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });

  const windowDays = Number(req.nextUrl.searchParams.get("window_days") ?? "30");
  const workspace = req.nextUrl.searchParams.get("workspace");
  const scope = resolveSessionScope(session);
  return Response.json(
    buildCostBreakdown(windowDays, workspace, scope.isOrgWide ? null : scope.businessUnitIds),
  );
}
