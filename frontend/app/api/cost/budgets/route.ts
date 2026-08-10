import { type NextRequest } from "next/server";

import { buildBudgets } from "@/lib/mock/cost-fixtures";
import { patchWorkspace } from "@/lib/mock/workspace-fixtures";
import { getSession } from "@/lib/auth/session";
import { resolveSessionScope, type AccessScope } from "@/lib/auth/access-scope";
import { canManageBusinessUnit } from "@/lib/mock/access-scope";

// DUMMY-DATA SEAM: derives fixtures directly from the workspace/project
// fixtures. When the FastAPI cost_router budget endpoints land, replace both
// bodies with bffProxy("/cost/budgets", ...).
//
// SCOPE FILTER: budget rows name every unit and project by id with its cap and
// current spend — the same disclosure as /api/cost, in a more convenient shape.
const allowedFor = (scope: AccessScope) =>
  scope.isOrgWide
    ? null
    : { workspaceIds: scope.businessUnitIds, projectIds: scope.projectIds };

export async function GET() {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });
  return Response.json(buildBudgets(allowedFor(resolveSessionScope(session))));
}

export async function PUT(req: NextRequest) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });

  const scope = resolveSessionScope(session);
  const body = (await req.json()) as {
    scope: "org" | "workspace" | "project";
    id: string;
    monthlyBudgetUsd: number | null;
  };
  // Only workspace budgets persist in this fixture set today — org is a
  // derived total and project fixtures (mocks/fixtures.ts) have no mutable
  // store yet, matching the read-only project list elsewhere in mock mode.
  //
  // MANAGE, not read: a Project Admin can see their parent unit's cap for
  // context but must not be able to raise it. A Business Unit Admin raising
  // their own cap goes through the governance-approval flow
  // (POST /workspaces/:id/budget-increase-request), never this endpoint.
  if (body.scope === "workspace") {
    if (!canManageBusinessUnit(scope, body.id)) {
      return Response.json({ code: "not_found", message: "not found" }, { status: 404 });
    }
    patchWorkspace(body.id, { monthlyBudgetUsd: body.monthlyBudgetUsd ?? undefined });
  }
  return Response.json(buildBudgets(allowedFor(scope)));
}
