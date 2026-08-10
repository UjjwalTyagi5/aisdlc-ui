import { type NextRequest } from "next/server";

import { getWorkspace, patchWorkspace } from "@/lib/mock/workspace-fixtures";

import { getSession } from "@/lib/auth/session";
import { resolveSessionScope } from "@/lib/auth/access-scope";
import { canManageBusinessUnit, canReadBusinessUnit } from "@/lib/mock/access-scope";

// DUMMY-DATA SEAM: mirrors mocks/handlers.ts — see [[msw-dual-runtime-mutation-rule]].

// SCOPE FILTER: /workspaces/:id is directly guessable, so removing a sibling
// unit from the list is only half the boundary. 404 rather than 403, for the
// same reason as the project route: a 403 confirms the unit exists.
export async function GET(
  _req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });
  const { id } = await params;
  const ws = getWorkspace(id);
  if (!ws || !canReadBusinessUnit(resolveSessionScope(session), id)) {
    return Response.json({ code: "not_found", message: "not found" }, { status: 404 });
  }
  return Response.json(ws);
}

/**
 * The write path checks MANAGE, not read. The two differ for exactly the case
 * this whole change exists to handle: a Project Admin can read the parent unit
 * of their own project — they need its name, cap and connectors for context —
 * but must never be able to rename it, re-classify its data or move its budget.
 */
export async function PATCH(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });
  const { id } = await params;
  const scope = resolveSessionScope(session);
  if (!canManageBusinessUnit(scope, id)) {
    return Response.json({ code: "not_found", message: "not found" }, { status: 404 });
  }
  const patch = (await req.json()) as Record<string, unknown>;
  // `isActive` is the one field a unit's own Admin may not touch: marking a
  // unit active or inactive is the Org Admin's call about the unit, not a
  // setting the unit administers for itself. 403 rather than 404 here — unlike
  // the scope miss above, the caller can already see this unit, so there is
  // nothing left to conceal by pretending it doesn't exist.
  if ("isActive" in patch && !scope.isOrgWide) {
    return Response.json(
      { code: "forbidden", message: "Only an Organization Admin can change active status" },
      { status: 403 },
    );
  }
  // Budget cascade (PRD §34.5): a unit's own Admin may set the FIRST cap — the
  // Org Admin is allowed to create a unit without one, and someone has to be
  // able to fill in the blank. Changing a cap that already exists is a
  // different act: there is a prior figure, so it goes through the approval
  // flow at POST /workspaces/:id/budget-increase-request instead.
  const touchesBudget =
    "monthlyBudgetUsd" in patch || "budgetStartDate" in patch || "budgetEndDate" in patch;
  if (touchesBudget && !scope.isOrgWide) {
    const current = getWorkspace(id);
    if ((current?.monthlyBudgetUsd ?? null) !== null) {
      return Response.json(
        {
          code: "forbidden",
          message: "Request a budget increase — an existing cap needs Org Admin approval to change",
        },
        { status: 403 },
      );
    }
  }
  const ws = patchWorkspace(id, patch);
  if (!ws) return Response.json({ code: "not_found", message: "not found" }, { status: 404 });
  return Response.json(ws);
}
