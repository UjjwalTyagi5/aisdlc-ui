import { type NextRequest } from "next/server";

import { getSession } from "@/lib/auth/session";
import { resolveSessionScope } from "@/lib/auth/access-scope";
import { getWorkspace, setBusinessUnitAdmin } from "@/lib/mock/workspace-fixtures";
import { addAccessMember } from "@/lib/mock/access-fixtures";
import { BUSINESS_UNIT_LABEL } from "@/lib/scope";

// DUMMY-DATA SEAM: mirrors mocks/handlers.ts — see [[msw-dual-runtime-mutation-rule]].

/**
 * Re-appoint a Business Unit's admin (PRD §15.2).
 *
 * Org Admin only, and 403 rather than the 404 the sibling routes use: a unit's
 * own Admin can see this unit perfectly well, so there is nothing to conceal —
 * what they cannot do is choose their own replacement. `scope.isOrgWide` is the
 * check rather than `canManageBusinessUnit`, which the sitting BU Admin passes.
 */
export async function POST(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });

  const { id } = await params;
  if (!resolveSessionScope(session).isOrgWide) {
    return Response.json(
      {
        code: "forbidden",
        message: `Only an Organization Admin can change a ${BUSINESS_UNIT_LABEL.toLowerCase()}'s admin`,
      },
      { status: 403 },
    );
  }

  const body = (await req.json().catch(() => ({}))) as { email?: string; displayName?: string };
  if (!body?.email) {
    return Response.json({ code: "invalid_input", message: "email is required" }, { status: 422 });
  }
  if (!getWorkspace(id)) {
    return Response.json({ code: "not_found", message: "not found" }, { status: 404 });
  }

  const result = setBusinessUnitAdmin(id, { email: body.email, displayName: body.displayName });
  if (!result) return Response.json({ code: "not_found", message: "not found" }, { status: 404 });

  // Keep the Roles & Access screen's separate roster in sync — it doesn't read
  // from workspace-fixtures.ts (same reason as app/api/onboarding/route.ts).
  addAccessMember(
    id,
    {
      userId: result.admin.ssoSubject,
      name: result.admin.displayName,
      email: result.admin.email,
    },
    "bu_admin",
  );

  return Response.json({
    workspaceId: id,
    admin: {
      identityId: result.admin.id,
      userId: result.admin.ssoSubject,
      email: result.admin.email,
      displayName: result.admin.displayName,
      initials: result.admin.initials,
    },
    replacedDisplayName: result.replaced?.displayName ?? null,
  });
}
