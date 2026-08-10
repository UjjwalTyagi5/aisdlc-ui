import { type NextRequest } from "next/server";

import { CONNECTORS } from "@/mocks/fixtures";
import { visibleConnectorsForScope } from "@/lib/mock/connector-scope";
import { getSession } from "@/lib/auth/session";
import { resolveSessionScope } from "@/lib/auth/access-scope";

// DUMMY-DATA SEAM: returns the fixture list directly — a critical-path route
// (integrations hub, connector pickers) that must work even if MSW never
// intercepts it. Mirrored in mocks/handlers.ts — see
// [[msw-dual-runtime-mutation-rule]].
//
// force-dynamic: the response varies by `workspaceId`, and even without one
// Next's Full Route Cache would otherwise statically cache the first response
// to disk — see the identical note on app/api/agent-profiles/summary/route.ts.
export const dynamic = "force-dynamic";

export async function GET(req: NextRequest) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });

  // Org-wide connectors plus the requesting unit's own — intersected with the
  // units the viewer may read. The old "no workspaceId means the whole tenant"
  // default is retained ONLY for an unbounded (org-wide) viewer; for anyone else
  // it is exactly the leak, because the dashboard health tile and the onboarding
  // wizard both call without an id. See visibleConnectorsForScope.
  const workspaceId = req.nextUrl.searchParams.get("workspaceId");
  const scope = resolveSessionScope(session);
  return Response.json(
    visibleConnectorsForScope(
      CONNECTORS,
      workspaceId,
      scope.isOrgWide ? null : scope.businessUnitIds,
    ),
  );
}
