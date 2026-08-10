import { getModelCatalog } from "@/lib/mock/model-fixtures";

// DUMMY-DATA SEAM: returns the fixture catalog directly. Mirrored in
// mocks/handlers.ts — see [[msw-dual-runtime-mutation-rule]].
//
// force-dynamic: no request param/dynamic API used, so Next's Full Route
// Cache would otherwise statically cache the first response to disk — see
// the identical note on app/api/agent-profiles/summary/route.ts.
export const dynamic = "force-dynamic";

export function GET() {
  return Response.json(getModelCatalog());
}
