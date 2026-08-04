import { type NextRequest } from "next/server";

import {
  createGovernanceApproval,
  listGovernanceApprovals,
} from "@/lib/mock/governance-approval-fixtures";
import { getSession } from "@/lib/auth/session";
import { resolveSessionScope, sessionIdentityId } from "@/lib/auth/access-scope";
import { effectivePlatformRole } from "@/lib/auth/effective-role";
import { canReadGovernanceApproval } from "@/lib/mock/access-scope";
import { canRaiseRequest, canRaiseType } from "@/lib/requests/routing";
import { ROLE_META } from "@/lib/roles";
import { listWorkspaces } from "@/lib/mock/workspace-fixtures";
import { PROJECTS } from "@/mocks/fixtures";
import { RequestCreateInput, REQUEST_TYPE_LABEL } from "@/lib/schemas/governance-approval";

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
  const items = listGovernanceApprovals(workspaceId).filter(
    (a) =>
      canReadGovernanceApproval(scope, a.workspaceId, a.projectId) ||
      // You always see what you raised, wherever it has climbed to. Without
      // this an initiator loses sight of their own request the moment it
      // escalates past the scope they can administer — which is exactly when
      // they most want to know where it went.
      (a.requestedById != null && a.requestedById === scope.identityId),
  );
  return Response.json(items);
}

/**
 * Raise a request.
 *
 * Enforced here, not only in the dialog: the Organization Admin cannot raise
 * one (nothing sits above them to decide it), and the requester's role and the
 * resulting approver are taken from the session rather than the body.
 */
export async function POST(req: NextRequest) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });

  const role = effectivePlatformRole(session);
  if (!canRaiseRequest(role)) {
    return Response.json(
      {
        code: "forbidden",
        message:
          "Organization Admins are the final approval authority and cannot raise requests.",
      },
      { status: 403 },
    );
  }

  const parsed = RequestCreateInput.safeParse(await req.json());
  if (!parsed.success) {
    return Response.json(
      { code: "invalid", message: parsed.error.issues[0]?.message ?? "Invalid request" },
      { status: 400 },
    );
  }
  const input = parsed.data;

  // Scoped by tier, not merely hidden in the picker. A request filed at the
  // wrong tier wastes the approver's time before it wastes the requester's —
  // and a client can post whatever type it likes.
  if (!canRaiseType(role, input.type)) {
    return Response.json(
      {
        code: "forbidden",
        message: `A ${role ? ROLE_META[role].label : "viewer"} cannot raise a ${REQUEST_TYPE_LABEL[input.type]} request.`,
      },
      { status: 403 },
    );
  }

  const scope = resolveSessionScope(session);
  // A request must be raised inside a scope the person actually holds —
  // otherwise the chain above it is somebody else's chain.
  if (!scope.isOrgWide && !scope.businessUnitIds.includes(input.workspaceId)) {
    return Response.json({ code: "not_found", message: "not found" }, { status: 404 });
  }

  const unit = listWorkspaces().find((w) => String(w.id) === input.workspaceId);
  if (!unit) return Response.json({ code: "not_found", message: "not found" }, { status: 404 });

  const project = input.projectId
    ? PROJECTS.find((p) => String(p.id) === input.projectId)
    : undefined;
  if (input.projectId && (!project || !scope.projectIds.includes(input.projectId))) {
    if (!scope.isOrgWide) {
      return Response.json({ code: "not_found", message: "not found" }, { status: 404 });
    }
  }

  const created = createGovernanceApproval({
    type: input.type,
    workspaceId: String(unit.id),
    workspaceName: unit.displayName,
    projectId: project ? String(project.id) : null,
    projectName: project?.name ?? null,
    title: input.title,
    summary: `${REQUEST_TYPE_LABEL[input.type]} requested by ${session.user.name}.`,
    description: input.description,
    priority: input.priority,
    attachments: input.attachments,
    requestedBy: session.user.name,
    requestedById: sessionIdentityId(session),
    requestedByRole: role,
    // Hand-raised requests carry no external target to apply on approval —
    // the decision is the outcome, not a switch to flip elsewhere.
    targetRef: input.projectId ?? String(unit.id),
  });

  return Response.json(created, { status: 201 });
}
