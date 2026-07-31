import { type NextRequest } from "next/server";

import { getSession } from "@/lib/auth/session";
import { getOrgAllowedModels, setOrgAllowedModels } from "@/lib/mock/model-fixtures";
import type { ModelAllowEntry } from "@/lib/schemas/model";

// DUMMY-DATA SEAM: mutates the shared fixture store directly. Mirrored in
// mocks/handlers.ts — see [[msw-dual-runtime-mutation-rule]].
//
// force-dynamic: GET takes no request param/dynamic API, so Next's Full
// Route Cache would otherwise statically cache the first response to disk —
// see the identical note on app/api/agent-profiles/summary/route.ts.
export const dynamic = "force-dynamic";

export function GET() {
  return Response.json(getOrgAllowedModels());
}

export async function PUT(req: NextRequest) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });
  const body = (await req.json()) as { entries: ModelAllowEntry[] };
  return Response.json(setOrgAllowedModels(body.entries ?? []));
}
