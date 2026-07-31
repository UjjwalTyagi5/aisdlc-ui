import { type NextRequest } from "next/server";

import { getSession } from "@/lib/auth/session";
import { getBuAllowedModels, setBuAllowedModels } from "@/lib/mock/model-fixtures";
import type { ModelAllowEntry } from "@/lib/schemas/model";

// DUMMY-DATA SEAM: mutates the shared fixture store directly. Mirrored in
// mocks/handlers.ts — see [[msw-dual-runtime-mutation-rule]].
export function GET(req: NextRequest) {
  const workspaceId = req.nextUrl.searchParams.get("workspaceId");
  if (!workspaceId) {
    return Response.json({ code: "invalid_input", message: "workspaceId is required" }, { status: 422 });
  }
  return Response.json(getBuAllowedModels(workspaceId));
}

export async function PUT(req: NextRequest) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });
  const workspaceId = req.nextUrl.searchParams.get("workspaceId");
  if (!workspaceId) {
    return Response.json({ code: "invalid_input", message: "workspaceId is required" }, { status: 422 });
  }
  const body = (await req.json()) as { entries: ModelAllowEntry[] };
  return Response.json(setBuAllowedModels(workspaceId, body.entries ?? []));
}
