import { type NextRequest } from "next/server";

import { getSession } from "@/lib/auth/session";
import { effectivePlatformRole } from "@/lib/auth/effective-role";
import { getOrgModelGrants, setOrgModelGrants } from "@/lib/mock/model-fixtures";
import type { OrgModelGrant } from "@/lib/schemas/model";

// DUMMY-DATA SEAM: mutates the shared fixture store directly. Mirrored in
// mocks/handlers.ts — see [[msw-dual-runtime-mutation-rule]].
//
// force-dynamic: GET takes no request param/dynamic API, so Next's Full
// Route Cache would otherwise statically cache the first response to disk —
// see the identical note on app/api/agent-profiles/summary/route.ts.
export const dynamic = "force-dynamic";

export function GET() {
  return Response.json(getOrgModelGrants());
}

export async function PUT(req: NextRequest) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });
  // The grant list IS the organization's catalogue policy — a Business Unit
  // Admin holding `model:manage` for their own unit must not be able to widen
  // what the organization permits, so the role, not the permission, is the
  // gate here.
  if (effectivePlatformRole(session) !== "org_admin") {
    return Response.json(
      { code: "forbidden", message: "Only an Organization Admin can change model grants." },
      { status: 403 },
    );
  }
  const body = (await req.json()) as { entries?: OrgModelGrant[] };
  return Response.json(setOrgModelGrants(body.entries ?? []));
}
