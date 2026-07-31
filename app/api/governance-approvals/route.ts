import { type NextRequest } from "next/server";

import { listGovernanceApprovals } from "@/lib/mock/governance-approval-fixtures";
import { getSession } from "@/lib/auth/session";
import { resolveSessionScope } from "@/lib/auth/access-scope";
import { canReadGovernanceApproval } from "@/lib/mock/access-scope";

// DUMMY-DATA SEAM: reads the in-memory fixture store directly. Mirrored in
// mocks/handlers.ts — see [[msw-dual-runtime-mutation-rule]]: the decide
// route mutates PROJECTS, which mocks/handlers.ts also serves via MSW for
// GET /api/projects, so both sides of this flow must run in the same
// runtime as each other.
//
// SCOPE FILTER: the `workspaceId` query param is the CALLER's narrowing choice
// (the queue's "mine"/"all" toggle) and must not be mistaken for the boundary —
// omitting it previously returned every unit's governance queue. The viewer's
// own scope is applied unconditionally afterwards, so "all" can only ever widen
// to the scopes they actually administer.
//
// `canReadGovernanceApproval` gates on ADMINISTERS, not reads: a Project Admin
// who may read their parent unit for context must not thereby see that unit's
// budget-increase requests.
export async function GET(req: NextRequest) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });

  const scope = resolveSessionScope(session);
  const workspaceId = req.nextUrl.searchParams.get("workspaceId") ?? undefined;
  const items = listGovernanceApprovals(workspaceId).filter((a) =>
    canReadGovernanceApproval(scope, a.workspaceId, a.projectId),
  );
  return Response.json(items);
}
