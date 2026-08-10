import { type NextRequest } from "next/server";

import { TRACES, buildMetrics } from "@/lib/mock/trace-fixtures";
import { getSession } from "@/lib/auth/session";
import { resolveSessionScope } from "@/lib/auth/access-scope";
import { filterByProject } from "@/lib/mock/access-scope";

// DUMMY-DATA SEAM: returns fixtures directly. When the Langfuse-backed live
// proxy lands, replace the body with: return bffProxy(`/traces/metrics?${search}`).
//
// SCOPE FILTER: computed over the same filtered set /api/traces returns, so the
// metrics strip can never claim more traces than the list below it shows.
export async function GET(req: NextRequest) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });

  const windowDays = Number(req.nextUrl.searchParams.get("window_days") ?? "30");
  const scope = resolveSessionScope(session);
  return Response.json(
    buildMetrics(windowDays, filterByProject(scope, TRACES, (t) => t.projectId)),
  );
}
