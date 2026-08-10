import { type NextRequest } from "next/server";

import { GATES } from "@/lib/mock/approval-fixtures";
import { getSession } from "@/lib/auth/session";
import { resolveSessionScope } from "@/lib/auth/access-scope";
import { filterByProject } from "@/lib/mock/access-scope";

// DUMMY-DATA SEAM: returns fixtures directly. When the Temporal-backed gate
// state lands, replace the body with: return bffProxy(`/approvals?${sp}`).
//
// SCOPE FILTER: a gate belongs to exactly one project, so "no cross-unit or
// cross-project visibility" is enforced here by dropping every gate outside the
// viewer's project set. This is the count the dashboard's "waiting on you" tile,
// the Approvals queue and the notification bell all derive from — filtering at
// the source is what keeps those numbers from disagreeing with the queue they
// link to.
//
// Which gates ROUTE to the viewer (by role and permission) is a separate,
// narrower question, answered client-side in
// components/app/approval-queue.tsx. Scope decides what may be seen at all;
// routing decides what is theirs to act on. Applying only the second would
// still show a Project Admin every other project's queue.
export async function GET(req: NextRequest) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });

  const scope = resolveSessionScope(session);
  const type = req.nextUrl.searchParams.get("type"); // approval | clarification
  let gates = filterByProject(scope, GATES, (g) => g.projectId);
  if (type === "approval" || type === "clarification") {
    gates = gates.filter((g) => g.type === type);
  }
  // Oldest first — SLA pressure rises to the top.
  gates = [...gates].sort(
    (a, b) => new Date(a.requestedAt).getTime() - new Date(b.requestedAt).getTime(),
  );
  return Response.json(gates);
}
