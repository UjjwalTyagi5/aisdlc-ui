import { type NextRequest } from "next/server";

import { createWorkspace, listWorkspaces } from "@/lib/mock/workspace-fixtures";
import { getSession } from "@/lib/auth/session";
import { resolveSessionScope } from "@/lib/auth/access-scope";
import { canReadBusinessUnit } from "@/lib/mock/access-scope";
import { BUSINESS_UNIT_LABEL } from "@/lib/scope";

// DUMMY-DATA SEAM: reads/writes the shared workspace-fixtures store directly —
// this is a critical-path route (sidebar, active-workspace resolution) hit on
// nearly every page, so it must work even if MSW never intercepts it (e.g. a
// stale/unregistered Service Worker) rather than depend solely on the mock
// browser layer. Mirrored in mocks/handlers.ts — see
// [[msw-dual-runtime-mutation-rule]].
// SCOPE FILTER: a Business Unit Admin must see only its own unit(s), and a
// contributor only the unit(s) its projects sit in. This list feeds the sidebar
// switcher, every Business Unit dropdown and the org dashboard, so filtering it
// here closes the widest leak in one place — a sibling unit that never reaches
// the browser cannot be picked, searched, filtered or linked to.
export async function GET() {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });
  const scope = resolveSessionScope(session);
  return Response.json(listWorkspaces().filter((w) => canReadBusinessUnit(scope, String(w.id))));
}

export async function POST(req: NextRequest) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });
  const body = (await req.json()) as {
    displayName: string;
    businessUnit?: string;
    costCenter?: string;
    /** Optional at creation; null = no cap, for the unit's Admin to set later. */
    monthlyBudgetUsd?: number | null;
    budgetStartDate?: string | null;
    budgetEndDate?: string | null;
    /** Org-Admin-only marker; defaults to active. */
    isActive?: boolean;
  };
  if (!body?.displayName || body.displayName.trim().length < 2) {
    return Response.json(
      { code: "invalid_input", message: `${BUSINESS_UNIT_LABEL} name must be at least 2 characters` },
      { status: 422 },
    );
  }
  const created = createWorkspace({ ...body, displayName: body.displayName.trim() });
  return Response.json(created, { status: 201 });
}
