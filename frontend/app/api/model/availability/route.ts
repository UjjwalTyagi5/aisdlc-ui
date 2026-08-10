import { type NextRequest } from "next/server";

import { getSession } from "@/lib/auth/session";
import { resolveSessionScope } from "@/lib/auth/access-scope";
import { getBuModelAvailability } from "@/lib/mock/model-fixtures";

// DUMMY-DATA SEAM: reads the shared grant + provider stores. Mirrored in
// mocks/handlers.ts — see [[msw-dual-runtime-mutation-rule]].
export const dynamic = "force-dynamic";

/**
 * What one Business Unit may use and whether anything still needs a key.
 *
 * The plain allow-list (`/model/allowed/bu`) answers "may we?"; this also
 * answers "must we do anything?", which is what a BU or Project Admin actually
 * arrives with — a centrally credentialed model needs no setup from them at
 * all, and offering one a credentials form is the failure this endpoint exists
 * to prevent.
 */
export async function GET(req: NextRequest) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });

  const workspaceId = req.nextUrl.searchParams.get("workspaceId");
  if (!workspaceId) {
    return Response.json({ code: "invalid_input", message: "workspaceId is required" }, { status: 422 });
  }

  // Credential coverage is unit-level detail; a viewer who can't read the unit
  // can't read what it's missing either.
  const scope = resolveSessionScope(session);
  if (!scope.isOrgWide && !scope.businessUnitIds.includes(String(workspaceId))) {
    return Response.json({ code: "not_found" }, { status: 404 });
  }

  return Response.json(getBuModelAvailability(workspaceId));
}
