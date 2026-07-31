import { ACCESS_WORKSPACES } from "@/lib/mock/access-fixtures";
import { getSession } from "@/lib/auth/session";
import { resolveSessionScope } from "@/lib/auth/access-scope";
import { canManageBusinessUnit } from "@/lib/mock/access-scope";

// DUMMY-DATA SEAM: was an unconditional `bffFetch("/admin/workspaces")`, which
// 500s with no FastAPI running — see [[dummy-data-seam-pattern]]. Reads the
// shared ACCESS_WORKSPACES store, matching the MSW handler for the same path so
// both runtimes agree.
//
// SCOPE FILTER: this list is the Business Unit picker on Roles & Access, so it
// decides whose role assignments an admin can even open. MANAGE, not read — a
// Project Admin who may read their parent unit for context must not be offered
// it as a unit whose memberships they can edit.
export async function GET() {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });

  const scope = resolveSessionScope(session);
  return Response.json(ACCESS_WORKSPACES.filter((w) => canManageBusinessUnit(scope, w.id)));
}
