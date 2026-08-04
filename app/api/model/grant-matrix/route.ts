import { getSession } from "@/lib/auth/session";
import { effectivePlatformRole } from "@/lib/auth/effective-role";
import { getModelGrantMatrix } from "@/lib/mock/model-fixtures";

// DUMMY-DATA SEAM: derives from the catalogue, the org grants and the provider
// connections. Mirrored in mocks/handlers.ts — see
// [[msw-dual-runtime-mutation-rule]]: the grants this reads are written by
// POST /api/model/allowed/org, so both must run in the same runtime.
//
// ORG ADMIN ONLY, and not merely because the page is theirs. The matrix names
// every Business Unit's standing against every model — who has what, and who
// has quietly keyed something themselves. That is the whole organization's
// posture in one payload, and it belongs to the tier that sets it.
export async function GET() {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });

  if (effectivePlatformRole(session) !== "org_admin") {
    return Response.json({ code: "forbidden", message: "not found" }, { status: 403 });
  }

  return Response.json(getModelGrantMatrix());
}
