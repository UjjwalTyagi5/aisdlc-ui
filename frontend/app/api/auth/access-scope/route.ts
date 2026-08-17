import { ApiRequestError } from "@/lib/api/client";
import { getSession } from "@/lib/auth/session";
import { effectivePlatformRole } from "@/lib/auth/effective-role";
import { bffFetch } from "@/lib/bff/client";
import type { AccessScopeOut, ScopeBinding } from "@/lib/schemas/access-scope";

/**
 * The viewer's resolved access scope — WHICH Business Units and projects they may
 * see, as opposed to WHAT actions they may take (that is `permissions`).
 *
 * A critical-path route: the sidebar, every scoped list and every scope indicator
 * depend on it.
 *
 * IT NOW RESOLVES FROM THE BACKEND, which is the whole point. It used to call
 * `resolveSessionScope`, and that function's org-wide branch built the answer out
 * of the fixture `listWorkspaces()` and `PROJECTS` arrays — so an Organization
 * Admin's scope named units and projects the database has never held, and every
 * page that filtered by it inherited them. Worse, a real signed-in person whose
 * email matched no seeded persona resolved to an EMPTY scope, so a genuine
 * Business Unit Admin saw "no access yet" over units they actually administer.
 *
 * `GET /workspaces` and `GET /projects` already answer this question. Both are
 * scoped by the backend against real bindings — org-wide callers get everything,
 * everyone else gets what they hold a binding for — which is the definition of
 * this endpoint's `businessUnitIds` and `projectIds`.
 *
 * THE MANAGED SETS ARE DERIVED FROM THE ROLE, not from a per-binding read,
 * because FastAPI exposes no endpoint that lists a caller's own bindings with
 * their roles. The derivation is sound rather than a guess: a `bu_admin` is bound
 * to the units they administer and to no others, so the units the backend returned
 * ARE their managed set. Same for a `project_admin` and their projects. Every
 * other role manages neither.
 *
 * BACKLOG: FastAPI `GET /auth/access-scope` (or bindings on `/auth/me`), which
 * would make this a passthrough. The response SHAPE
 * (lib/schemas/access-scope.ts) is the contract and does not change either way.
 */
export const dynamic = "force-dynamic";

interface WorkspaceRow {
  id: string;
  displayName: string;
}

interface ProjectRow {
  id: string;
  name: string;
  workspaceId?: string | null;
}

export async function GET() {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });

  const role = effectivePlatformRole(session);
  const isOrgWide = session.permissions?.includes("admin:*") ?? false;

  try {
    const [units, projectPage] = await Promise.all([
      bffFetch("/workspaces", { session }) as Promise<WorkspaceRow[]>,
      bffFetch("/projects", { session }) as Promise<{ items?: ProjectRow[] } | ProjectRow[]>,
    ]);
    const projects = Array.isArray(projectPage) ? projectPage : (projectPage.items ?? []);

    const businessUnitIds = units.map((u) => String(u.id));
    const projectIds = projects.map((p) => String(p.id));
    const unitName = new Map(units.map((u) => [String(u.id), u.displayName]));

    const unitBindings: ScopeBinding[] = units.map((u) => ({
      kind: "business_unit",
      scopeId: String(u.id),
      scopeName: u.displayName,
      role: role ?? "contributor",
      parentId: null,
      parentName: null,
      status: "active",
    }));

    const projectBindings: ScopeBinding[] = projects.map((p) => ({
      kind: "project",
      scopeId: String(p.id),
      scopeName: p.name,
      role: role ?? "contributor",
      parentId: p.workspaceId ? String(p.workspaceId) : null,
      parentName: p.workspaceId ? (unitName.get(String(p.workspaceId)) ?? null) : null,
      status: "active",
    }));

    const bindings = [...unitBindings, ...projectBindings];

    const scope: AccessScopeOut = {
      level: isOrgWide ? "organization" : businessUnitIds.length > 0 ? "business_unit" : "project",
      isOrgWide,
      businessUnitIds,
      managedBusinessUnitIds:
        isOrgWide || role === "bu_admin" ? businessUnitIds : [],
      projectIds,
      managedProjectIds:
        isOrgWide || role === "bu_admin" || role === "project_admin" ? projectIds : [],
      actingBindings: bindings,
      allBindings: bindings,
      // The backend keys people by their user id; there is no separate identity
      // store for it to differ from any more.
      identityId: session.user.id ?? null,
    };

    return Response.json(scope);
  } catch (err) {
    if (err instanceof ApiRequestError) {
      return Response.json(
        err.details ?? { code: err.code, message: err.message },
        { status: err.status },
      );
    }
    throw err;
  }
}
