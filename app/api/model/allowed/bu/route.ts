import { type NextRequest } from "next/server";

import { getSession } from "@/lib/auth/session";
import { effectivePlatformRole } from "@/lib/auth/effective-role";
import { getBuAllowedModels, setBuModelGrants } from "@/lib/mock/model-fixtures";
import type { ModelAllowEntry } from "@/lib/schemas/model";

// DUMMY-DATA SEAM: mutates the shared fixture store directly. Mirrored in
// mocks/handlers.ts — see [[msw-dual-runtime-mutation-rule]].
export const dynamic = "force-dynamic";

/** What this unit may use — derived from the org grants, so a BU Admin reading
 *  it sees exactly what was given to them and nothing they chose themselves. */
export function GET(req: NextRequest) {
  const workspaceId = req.nextUrl.searchParams.get("workspaceId");
  if (!workspaceId) {
    return Response.json({ code: "invalid_input", message: "workspaceId is required" }, { status: 422 });
  }
  return Response.json(getBuAllowedModels(workspaceId));
}

/**
 * Grant this unit a set of models. This is the Org Admin's per-unit control
 * (from Business Unit creation or management), NOT the unit's own —
 * a BU Admin narrowing their own grant would be indistinguishable from the
 * Org Admin revoking it, and only one of those should be possible.
 */
export async function PUT(req: NextRequest) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });
  if (effectivePlatformRole(session) !== "org_admin") {
    return Response.json(
      {
        code: "forbidden",
        message: "Only an Organization Admin can grant models to a business unit.",
      },
      { status: 403 },
    );
  }
  const workspaceId = req.nextUrl.searchParams.get("workspaceId");
  if (!workspaceId) {
    return Response.json({ code: "invalid_input", message: "workspaceId is required" }, { status: 422 });
  }
  const body = (await req.json()) as { entries?: ModelAllowEntry[] };
  return Response.json(setBuModelGrants(workspaceId, body.entries ?? []));
}
