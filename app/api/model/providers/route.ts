import { type NextRequest } from "next/server";

import { getSession } from "@/lib/auth/session";
import { effectivePlatformRole } from "@/lib/auth/effective-role";
import { createModelProvider, listModelProviders, type CreateModelProviderInput } from "@/lib/mock/model-fixtures";

// DUMMY-DATA SEAM: reads/writes the shared PROVIDERS array directly —
// approval-aware, mirroring app/api/projects/route.ts. See
// [[msw-dual-runtime-mutation-rule]]: mocks/handlers.ts's /api/model/providers
// mirrors this exactly.
export function GET(req: NextRequest) {
  const workspaceId = req.nextUrl.searchParams.get("workspaceId");
  return Response.json(listModelProviders(workspaceId || null));
}

export async function POST(req: NextRequest) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });

  const body = (await req.json()) as CreateModelProviderInput;
  if (!body?.provider || !body?.display_name) {
    return Response.json(
      { code: "invalid_input", message: "provider and display_name are required" },
      { status: 422 },
    );
  }

  const role = effectivePlatformRole(session);
  const created = createModelProvider(body, { role, displayName: session.user.name });
  return Response.json(created, { status: 201 });
}
