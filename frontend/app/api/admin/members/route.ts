import { type NextRequest } from "next/server";

import { ACCESS_MEMBERS, ACCESS_WORKSPACES } from "@/lib/mock/access-fixtures";
import { ApiRequestError } from "@/lib/api/client";
import { bffFetch } from "@/lib/bff/client";
import { getSession } from "@/lib/auth/session";
import { resolveSessionScope } from "@/lib/auth/access-scope";
import { canManageBusinessUnit } from "@/lib/mock/access-scope";

// DUMMY-DATA SEAM (GET): was an unconditional `bffFetch("/admin/members")`,
// which 500s with no FastAPI running — see [[dummy-data-seam-pattern]]. Reads
// the shared ACCESS_MEMBERS store, matching the MSW handler for the same path.
//
// SCOPE FILTER: the roster of who holds which role inside a unit names people,
// emails and roles — the payload this change exists to contain. `workspace_id`
// is caller-supplied, so an unauthorized unit answers with an EMPTY roster
// rather than falling through to a default one: silently substituting a
// different unit's members for a denied request is worse than either allowing
// or refusing it outright.
export async function GET(req: NextRequest) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });

  const scope = resolveSessionScope(session);
  const requested = req.nextUrl.searchParams.get("workspace_id");
  const manageable = ACCESS_WORKSPACES.filter((w) => canManageBusinessUnit(scope, w.id));
  const workspaceId = requested ?? manageable[0]?.id;

  if (!workspaceId || !canManageBusinessUnit(scope, workspaceId)) {
    return Response.json([]);
  }
  return Response.json(ACCESS_MEMBERS[workspaceId] ?? []);
}

/** Create a member (email+password) + role assignment (Phase 4). */
export async function POST(req: NextRequest) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });

  const body: unknown = await req.json();

  // Refuse a write into a unit the caller doesn't administer before it reaches
  // the backend. Reading a sibling unit is a disclosure; creating a member
  // inside one is an escalation — so this guard matters more than the GET's.
  const target =
    body && typeof body === "object"
      ? ((body as { workspace_id?: unknown }).workspace_id ?? null)
      : null;
  if (
    typeof target === "string" &&
    !canManageBusinessUnit(resolveSessionScope(session), target)
  ) {
    return Response.json({ code: "not_found", message: "not found" }, { status: 404 });
  }

  try {
    const data = await bffFetch("/admin/members", { session, method: "POST", body });
    return Response.json(data, { status: 201 });
  } catch (err) {
    if (err instanceof ApiRequestError) {
      return Response.json(err.details ?? { message: err.message }, { status: err.status });
    }
    throw err;
  }
}
