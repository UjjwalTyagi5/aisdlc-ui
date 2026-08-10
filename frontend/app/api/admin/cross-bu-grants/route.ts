import { type NextRequest } from "next/server";

import { getSession } from "@/lib/auth/session";
import { resolveSessionScope } from "@/lib/auth/access-scope";
import { canManageBusinessUnit } from "@/lib/mock/access-scope";
import { listCrossBuGrants, revokeCrossBuGrant } from "@/lib/mock/cross-bu-fixtures";
import { getProjectById } from "@/lib/mock/project-fixtures";
import { removeProjectMembershipsInWorkspace } from "@/lib/mock/project-membership-fixtures";
import { getIdentity, getWorkspace } from "@/lib/mock/workspace-fixtures";

// DUMMY-DATA SEAM: reads/writes the in-memory grant store. Mirrored in
// mocks/handlers.ts — see [[msw-dual-runtime-mutation-rule]].

/**
 * Every live loan that touches a unit the viewer administers — the ones they
 * have lent OUT, and the ones borrowed IN.
 *
 * Both directions, because an admin needs both answers and they are the same
 * fact seen from two sides: "who of mine is working elsewhere" and "whose
 * people are working here".
 */
export async function GET() {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });

  const scope = resolveSessionScope(session);
  const visible = listCrossBuGrants().filter(
    (g) =>
      canManageBusinessUnit(scope, g.parentWorkspaceId) ||
      canManageBusinessUnit(scope, g.targetWorkspaceId),
  );

  return Response.json(
    visible.map((g) => ({
      ...g,
      displayName: getIdentity(g.identityId)?.displayName ?? g.identityId,
      projectName: getProjectById(g.projectId)?.name ?? g.projectId,
      parentWorkspaceName: getWorkspace(g.parentWorkspaceId)?.displayName ?? g.parentWorkspaceId,
      targetWorkspaceName: getWorkspace(g.targetWorkspaceId)?.displayName ?? g.targetWorkspaceId,
      /** The viewer lent this person out, as opposed to borrowing them. */
      lentByYou: canManageBusinessUnit(scope, g.parentWorkspaceId),
    })),
  );
}

/**
 * End a loan.
 *
 * THE LENDER'S CALL, not the borrower's. Ownership never left the parent unit,
 * so its admin can always take their person back — that is what "ownership
 * remains with the parent Business Unit" has to mean in practice, or approving
 * would be a one-way door. The borrowing side can still drop the seat from its
 * own project Members screen; this is the other end of the same string.
 *
 * Revoking removes the seat as well as the grant. Leaving the seat behind would
 * produce a project member whose access is no longer authorised — the precise
 * state `projectMembershipBlock` exists to keep out.
 */
export async function DELETE(req: NextRequest) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });

  const body = (await req.json().catch(() => ({}))) as {
    identityId?: string;
    projectId?: string;
  };
  if (!body.identityId || !body.projectId) {
    return Response.json(
      { code: "invalid_input", message: "identityId and projectId are required" },
      { status: 422 },
    );
  }

  const grant = listCrossBuGrants().find(
    (g) => g.identityId === body.identityId && g.projectId === body.projectId,
  );
  if (!grant) return Response.json({ code: "not_found" }, { status: 404 });

  const scope = resolveSessionScope(session);
  if (!canManageBusinessUnit(scope, grant.parentWorkspaceId)) {
    return Response.json(
      {
        code: "forbidden",
        message: "Only the business unit that lent this person can end the loan.",
      },
      { status: 403 },
    );
  }

  revokeCrossBuGrant(body.identityId, body.projectId);
  removeProjectMembershipsInWorkspace(body.identityId, grant.targetWorkspaceId);
  return Response.json({ ok: true });
}
