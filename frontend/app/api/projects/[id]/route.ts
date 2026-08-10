import { type NextRequest } from "next/server";

import { getProjectById, updateProjectRecord, type ProjectUpdatePatch } from "@/lib/mock/project-fixtures";
import { getSession } from "@/lib/auth/session";
import { resolveSessionScope } from "@/lib/auth/access-scope";
import {
  canManageBusinessUnit,
  canManageProject,
  canReadProject,
} from "@/lib/mock/access-scope";

// DUMMY-DATA SEAM: reads/writes the shared PROJECTS array directly — a
// critical-path route (every project page depends on GET). Mirrored in
// mocks/handlers.ts — see [[msw-dual-runtime-mutation-rule]].

/**
 * Hiding a project from every list is not enough on its own: a guessed or
 * shared URL still resolves, and every one of the ~20 project sub-pages loads
 * from this endpoint. Guarding here closes all of them at once.
 *
 * An unauthorized project answers 404, not 403 — a 403 confirms the id exists,
 * which is itself the cross-project fact being withheld. Indistinguishable from
 * "no such project" is the correct answer to someone who may not know it exists.
 */
export async function GET(
  _req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });

  const { id } = await params;
  const project = getProjectById(id);
  const scope = resolveSessionScope(session);
  if (!project || !canReadProject(scope, id)) {
    return Response.json({ code: "not_found", message: "Project not found" }, { status: 404 });
  }
  return Response.json(project);
}

export async function PATCH(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });

  const { id } = await params;
  const scope = resolveSessionScope(session);
  // Same boundary on the write path — a scoped viewer must not be able to
  // rename or reconfigure a project they cannot even see.
  if (!canReadProject(scope, id)) {
    return Response.json({ code: "not_found", message: "not found" }, { status: 404 });
  }
  const body = (await req.json()) as ProjectUpdatePatch;
  // Delivery status and the cost cap are the project's own governance, so they
  // need MANAGE where the rest of this patch only needs read: a contributor who
  // can open a project must not be able to declare it completed or move its
  // budget. The BU clause is what makes this match the intended set — a
  // Business Unit Admin administers the projects in their unit without
  // necessarily holding a per-project binding, so `canManageProject` alone
  // would lock out one of the three roles that is supposed to have this.
  const needsManage =
    "deliveryStatus" in body ||
    "monthlyBudgetUsd" in body ||
    "budgetStartDate" in body ||
    "budgetEndDate" in body;
  if (needsManage) {
    const project = getProjectById(id);
    const managed =
      canManageProject(scope, id) || canManageBusinessUnit(scope, project?.workspaceId);
    if (!managed) {
      return Response.json(
        {
          code: "forbidden",
          message: "Only a Project, Business Unit or Organization Admin can change this",
        },
        { status: 403 },
      );
    }
  }
  const project = updateProjectRecord(id, body);
  if (!project) return Response.json({ code: "not_found", message: "not found" }, { status: 404 });
  return Response.json(project);
}
