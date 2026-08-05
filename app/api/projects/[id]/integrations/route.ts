import { type NextRequest } from "next/server";

import { getSession } from "@/lib/auth/session";
import { resolveSessionScope } from "@/lib/auth/access-scope";
import {
  listProjectIntegrations,
  upsertProjectCredential,
} from "@/lib/mock/project-integration-fixtures";
import { ProjectIntegrationCredentialInput } from "@/lib/schemas/project-integration";
import { PROJECTS } from "@/mocks/fixtures";

// DUMMY-DATA SEAM: reads/mutates the project-integration store directly.
// Mirrored in mocks/handlers.ts — see [[msw-dual-runtime-mutation-rule]].
export const dynamic = "force-dynamic";

/** Can this viewer see this project at all? Scope, not permission — "is in
 *  this project's unit" is a question about a binding. */
async function guard(id: string) {
  const session = await getSession();
  if (!session) return { error: Response.json({ code: "unauthenticated" }, { status: 401 }) };

  const project = PROJECTS.find((p) => String(p.id) === id);
  if (!project) return { error: Response.json({ code: "not_found" }, { status: 404 }) };

  const scope = resolveSessionScope(session);
  const reachable =
    scope.isOrgWide ||
    (project.workspaceId ? scope.businessUnitIds.includes(String(project.workspaceId)) : false);
  if (!reachable) return { error: Response.json({ code: "not_found" }, { status: 404 }) };

  return { session, project };
}

/**
 * The stable identity a credential belongs to. Falls back through the fields a
 * mock session may carry — never to a constant, which would make everyone one
 * owner again and undo the whole point of the key.
 */
function viewerId(session: { user?: { id?: string; email?: string; name?: string } }): string {
  const u = session.user;
  return u?.id ?? u?.email ?? u?.name ?? "unknown";
}

/** The integrations approved for this project, with the caller's credentials. */
export async function GET(_req: NextRequest, ctx: { params: Promise<{ id: string }> }) {
  const { id } = await ctx.params;
  const g = await guard(id);
  if (g.error) return g.error;
  // Scoped to the caller: a credential is the person's, not the project's.
  return Response.json(listProjectIntegrations(id, viewerId(g.session!)));
}

/**
 * Configure the project's credential against one approved integration.
 *
 * Deliberately open to contributors as well as the Project Admin: the person
 * wiring a repo bot into a pipeline is usually the one building the pipeline,
 * and the credential is scoped to a project they are already inside. What
 * they cannot do is change WHICH integrations the project has — that came
 * from the tiers above and is not editable here.
 */
export async function PUT(req: NextRequest, ctx: { params: Promise<{ id: string }> }) {
  const { id } = await ctx.params;
  const g = await guard(id);
  if (g.error) return g.error;

  const parsed = ProjectIntegrationCredentialInput.safeParse(await req.json());
  if (!parsed.success) {
    return Response.json(
      { code: "bad_request", message: parsed.error.issues[0]?.message ?? "Invalid credential." },
      { status: 400 },
    );
  }

  // Only against something the project actually has. A credential for an
  // integration nobody granted is a way to pre-stage access the cascade denied.
  const approved = listProjectIntegrations(id, viewerId(g.session!));
  const target = approved.find(
    (i) => i.kind === parsed.data.kind && i.id === parsed.data.targetId,
  );
  if (!target) {
    return Response.json(
      { code: "forbidden", message: "That integration is not approved for this project." },
      { status: 403 },
    );
  }

  const who = g.session!.user?.name ?? g.session!.user?.email ?? "Someone";
  return Response.json(upsertProjectCredential(id, parsed.data, who, viewerId(g.session!)));
}
