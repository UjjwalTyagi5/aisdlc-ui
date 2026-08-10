import { type NextRequest } from "next/server";

import { getSession } from "@/lib/auth/session";
import { resolveSessionScope } from "@/lib/auth/access-scope";
import { canManageBusinessUnit } from "@/lib/mock/access-scope";
import { assignBusinessUnitRole } from "@/lib/mock/onboarding";
import {
  findOrCreateIdentityBySsoSubject,
  removeMembership,
} from "@/lib/mock/workspace-fixtures";

type Params = Promise<{ id: string; userId: string }>;

/**
 * A role inside a unit is that unit's admin's to set.
 *
 * The people directory is org-wide now — a Business Unit Admin can see every
 * colleague and the role each one holds. That read is deliberate; this is the
 * boundary that keeps it a read. Without it, "other units are view-only" would
 * be a property of which buttons the page renders, and the page is not where
 * that decision belongs.
 */
async function guard(workspaceId: string) {
  const session = await getSession();
  if (!session) {
    return { session: null, error: Response.json({ code: "unauthenticated" }, { status: 401 }) };
  }
  if (!canManageBusinessUnit(resolveSessionScope(session), workspaceId)) {
    return {
      session,
      error: Response.json(
        { code: "forbidden", message: "You don't administer this business unit." },
        { status: 403 },
      ),
    };
  }
  return { session, error: null };
}

// DUMMY-DATA SEAM: the write itself lives in lib/mock/onboarding.ts so this
// handler and its MSW twin cannot drift — see [[msw-dual-runtime-mutation-rule]].
//
// It also closes any open `role_assignment` request for this person, which is
// why the shared function matters here: the admin can assign from Users or from
// Requests & Approvals, and the obligation has to be discharged by the write
// rather than by whichever button was pressed.
export async function PATCH(req: NextRequest, { params }: { params: Params }) {
  const { id, userId } = await params;
  const { session, error } = await guard(id);
  if (error) return error;

  const body = (await req.json().catch(() => ({}))) as { roleName?: string; roleLabel?: string };
  const { status, body: payload } = assignBusinessUnitRole({
    workspaceId: id,
    userId,
    roleName: body.roleName ?? "",
    roleLabel: body.roleLabel,
    actorName: session?.user?.name,
  });
  return Response.json(payload, { status });
}

export async function DELETE(_req: NextRequest, { params }: { params: Params }) {
  const { id, userId } = await params;
  const { error } = await guard(id);
  if (error) return error;

  const identity = findOrCreateIdentityBySsoSubject(userId);
  removeMembership(id, identity.id);
  return new Response(null, { status: 204 });
}
