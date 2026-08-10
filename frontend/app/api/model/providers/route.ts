import { type NextRequest } from "next/server";

import { getSession } from "@/lib/auth/session";
import { effectivePlatformRole } from "@/lib/auth/effective-role";
import {
  createModelProvider,
  listAllModelProviders,
  listModelProviders,
  type CreateModelProviderInput,
} from "@/lib/mock/model-fixtures";
import { resolveSessionScope } from "@/lib/auth/access-scope";

// DUMMY-DATA SEAM: reads/writes the shared PROVIDERS array directly —
// approval-aware, mirroring app/api/projects/route.ts. See
// [[msw-dual-runtime-mutation-rule]]: mocks/handlers.ts's /api/model/providers
// mirrors this exactly.
/**
 * `?scope=all` returns EVERY connection the caller may see — org-wide and
 * unit-scoped together — rather than exactly one scope.
 *
 * Model Management asked for `workspaceId=null`, which means "org-wide only",
 * so a subscription onboarded by a Business Unit was structurally invisible to
 * the Organization Admin. "Which providers is this organization actually
 * using?" is not a question one scope can answer, and the page asking it was
 * getting a confident wrong answer.
 */
export async function GET(req: NextRequest) {
  if (req.nextUrl.searchParams.get("scope") === "all") {
    const session = await getSession();
    if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });
    const scope = resolveSessionScope(session);
    return Response.json(
      listAllModelProviders(scope.isOrgWide ? null : scope.businessUnitIds),
    );
  }
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
