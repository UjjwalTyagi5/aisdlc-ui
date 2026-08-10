import { type NextRequest } from "next/server";

import { getSession } from "@/lib/auth/session";
import { effectivePlatformRole } from "@/lib/auth/effective-role";
import { resolveSessionScope, sessionIdentityId } from "@/lib/auth/access-scope";
import { canManageBusinessUnit, canManageProject } from "@/lib/mock/access-scope";
import { requestCrossBuAssignment } from "@/lib/mock/cross-bu";
import { getProjectById } from "@/lib/mock/project-fixtures";

// DUMMY-DATA SEAM: the transaction lives in lib/mock/cross-bu.ts so this and
// its MSW twin cannot drift — see [[msw-dual-runtime-mutation-rule]].
//
// WHY THIS ENDPOINT EXISTS RATHER THAN THE GENERIC `/api/governance-approvals`.
// A cross-unit request is only meaningful with a project, a person and a role
// in hand, and the place where all three are already known is the project's own
// Members screen. The generic raise form collects none of them, so offering the
// type there would ask someone to re-enter what they were just looking at — and
// the request would be routed off a `workspaceId` they picked, when the only
// correct one is the contributor's parent unit, which the server derives.
export async function POST(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });

  const { id } = await params;
  const project = getProjectById(id);
  if (!project) return Response.json({ code: "not_found" }, { status: 404 });

  /**
   * Only someone accountable for the project may ask for people on it — its
   * Project Admin, or the admin of the unit that owns it.
   *
   * A contributor on the project is deliberately excluded. Borrowing someone
   * commits another unit's headcount and this project's budget; a request that
   * anyone on the team could file would put that decision in front of the
   * lending admin without anyone on this side having agreed to it.
   */
  const scope = resolveSessionScope(session);
  const entitled =
    scope.isOrgWide ||
    canManageProject(scope, String(project.id)) ||
    canManageBusinessUnit(scope, project.workspaceId ? String(project.workspaceId) : null);
  if (!entitled) {
    return Response.json(
      {
        code: "forbidden",
        message: "Only this project's admin, or its business unit's, can ask for people on it.",
      },
      { status: 403 },
    );
  }

  const body = (await req.json().catch(() => ({}))) as {
    email?: string;
    roleName?: string;
    reason?: string;
  };

  const { status, body: payload } = requestCrossBuAssignment({
    projectId: String(project.id),
    email: body.email,
    roleName: body.roleName,
    reason: body.reason,
    actorName: session.user.name,
    actorIdentityId: sessionIdentityId(session),
    actorRole: effectivePlatformRole(session),
  });
  return Response.json(payload, { status });
}
