import { type NextRequest } from "next/server";

import { bffProxy } from "@/lib/bff/proxy";

/**
 * What one Business Unit may use and whether anything still needs a key —
 * proxied to FastAPI `GET /model/availability`.
 *
 * The plain allow-list (`/model/allowed/bu`) answers "may we?"; this also answers
 * "must we do anything?", which is what a BU or Project Admin actually arrives
 * with — a centrally credentialed model needs no setup from them at all, and
 * offering one a credentials form is the failure this endpoint exists to prevent.
 *
 * "Centrally credentialed" means an ORG-WIDE connection holds a key. A unit-scoped
 * key is not central however good it is, because it leaves every other unit still
 * owing one — the backend draws that line on `model_providers.workspace_id IS NULL`.
 */
export const dynamic = "force-dynamic";

export async function GET(req: NextRequest) {
  const workspaceId = req.nextUrl.searchParams.get("workspaceId");
  if (!workspaceId) {
    return Response.json(
      { code: "invalid_input", message: "workspaceId is required" },
      { status: 422 },
    );
  }
  return bffProxy(`/model/availability?workspaceId=${encodeURIComponent(workspaceId)}`);
}
