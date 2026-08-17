import { type NextRequest } from "next/server";

import { bffProxy } from "@/lib/bff/proxy";

/**
 * Members of a business unit — proxied to FastAPI `GET/POST /admin/members`.
 *
 * READ stays org-wide, deliberately. Users & Roles is an org-wide read with a
 * unit-scoped write: the two-step onboarding handover needs a Business Unit Admin
 * to find a person who is not yet in their unit, which a roster filtered to their
 * own unit makes impossible. Do not re-scope the GET.
 *
 * WRITE is now guarded in the backend rather than here. `member:manage` says the
 * caller manages members *somewhere*; `workspace_id` arrives in the body and was
 * never checked against that, so a Business Unit Admin could create members in any
 * sibling unit. FastAPI now refuses it with a 404, and this handler no longer
 * carries a check that could drift from it.
 */
export async function GET(req: NextRequest) {
  const workspaceId = req.nextUrl.searchParams.get("workspace_id");
  // workspace_id is required by the backend; without one there is no roster to
  // ask for, so answer with an empty list rather than a 422 the UI would surface
  // as an error on first paint.
  if (!workspaceId) return Response.json([]);
  return bffProxy(`/admin/members?workspace_id=${encodeURIComponent(workspaceId)}`);
}

export async function POST(req: NextRequest) {
  const body: unknown = await req.json();
  return bffProxy("/admin/members", { method: "POST", body });
}
