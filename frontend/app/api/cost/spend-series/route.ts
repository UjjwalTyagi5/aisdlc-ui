import { type NextRequest } from "next/server";

import { buildSpendSeries, type SpendGroupBy } from "@/lib/mock/cost-fixtures";
import { getSession } from "@/lib/auth/session";
import { resolveSessionScope } from "@/lib/auth/access-scope";
import { canReadBusinessUnit, canReadProject } from "@/lib/mock/access-scope";

// DUMMY-DATA SEAM: derives from the workspace/project fixtures — see
// lib/mock/cost-fixtures.ts. Mirrored in mocks/handlers.ts, per
// [[msw-dual-runtime-mutation-rule]].

const GROUP_BY = new Set<SpendGroupBy>(["business_unit", "project", "model", "provider"]);

export const dynamic = "force-dynamic";

/**
 * Monthly spend split by business unit, project or model.
 *
 * SCOPE FILTER: the caller's allowed units bound the result before their own
 * `workspaceId` choice narrows it, so "all" can only ever mean "all of mine" —
 * this is what lets a Business Unit Admin use the same chart and filters as an
 * Org Admin without seeing a sibling unit's spend. A `workspaceId` they cannot
 * read is refused rather than silently ignored, which would otherwise answer a
 * question about someone else's unit with org-wide totals.
 */
export async function GET(req: NextRequest) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });

  const scope = resolveSessionScope(session);
  const params = req.nextUrl.searchParams;

  const raw = params.get("groupBy") ?? "business_unit";
  const groupBy = (GROUP_BY.has(raw as SpendGroupBy) ? raw : "business_unit") as SpendGroupBy;

  const monthsRaw = Number(params.get("months") ?? 6);
  const months = Number.isFinite(monthsRaw) ? Math.min(24, Math.max(1, Math.trunc(monthsRaw))) : 6;

  const requested = params.get("workspaceId");
  const workspaceId = requested && requested !== "all" ? requested : null;
  if (workspaceId && !canReadBusinessUnit(scope, workspaceId)) {
    return Response.json({ code: "not_found", message: "not found" }, { status: 404 });
  }

  // A project's own Overview asks for exactly its own series. Refused rather
  // than ignored when unreadable, for the same reason as `workspaceId`: quietly
  // widening to every project would answer a question about someone else's
  // project with the viewer's own totals.
  const projectId = params.get("projectId");
  if (projectId && !canReadProject(scope, projectId)) {
    return Response.json({ code: "not_found", message: "not found" }, { status: 404 });
  }

  return Response.json(
    buildSpendSeries(
      months,
      scope.isOrgWide ? null : scope.businessUnitIds,
      groupBy,
      workspaceId,
      projectId,
    ),
  );
}
