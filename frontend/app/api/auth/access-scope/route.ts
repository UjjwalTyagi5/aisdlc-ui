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

/** Roles whose binding means "runs this scope", as opposed to working inside it.
 *
 *  A `project_admin` binding at BUSINESS-UNIT scope administers that unit's projects —
 *  which is how the platform actually grants a Project Admin their reach — so the same
 *  set answers for both kinds of scope. */
const ADMIN_ROLES = new Set(["org_admin", "bu_admin", "project_admin"]);

interface WorkspaceRow {
  id: string;
  displayName: string;
}

interface ProjectRow {
  id: string;
  name: string;
  workspaceId?: string | null;
}

/** One row of `GET /auth/bindings` — a scope the caller holds, and the role it grants. */
interface HeldBinding {
  kind: string;
  scopeId: string;
  role: string;
  status: string;
}

export async function GET() {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });

  const role = effectivePlatformRole(session);
  const isOrgWide = session.permissions?.includes("admin:*") ?? false;

  try {
    const [units, projectPage, heldBindings] = await Promise.all([
      bffFetch("/workspaces", { session }) as Promise<WorkspaceRow[]>,
      bffFetch("/projects", { session }) as Promise<{ items?: ProjectRow[] } | ProjectRow[]>,
      // The caller's REAL bindings, each with the role it actually grants. Fetched
      // server-side like the other two rather than proxied to the browser: nothing in
      // the UI wants this list on its own, and an endpoint that exists only to feed
      // this one is a smaller surface unexposed.
      (bffFetch("/auth/bindings", { session }) as Promise<HeldBinding[]>).catch(
        // DEGRADE, DO NOT FAIL. Without it the roles are less precise; without the
        // whole response the access page cannot render at all, and "no access" is a
        // far worse answer than "your role, imprecisely".
        () => [] as HeldBinding[],
      ),
    ]);
    const projects = Array.isArray(projectPage) ? projectPage : (projectPage.items ?? []);

    const businessUnitIds = units.map((u) => String(u.id));
    const projectIds = projects.map((p) => String(p.id));
    const unitName = new Map(units.map((u) => [String(u.id), u.displayName]));

    // THE ROLE A SCOPE ACTUALLY GRANTS, not the caller's one effective role stamped on
    // everything. That is what this file used to do, and it made a Project Admin in one
    // unit who merely CONTRIBUTES to a project in another read "Project Admin · You
    // administer" on both — an access page overstating authority, which is the one
    // direction it must not be wrong in.
    const heldRole = new Map(
      heldBindings.filter((b) => b.status === "active").map((b) => [b.scopeId, b.role]),
    );

    /** The role a UNIT is listed with when nothing is bound to the unit itself.
     *
     *  A unit can be in reach purely because of a project inside it — which is exactly
     *  how a Project Admin in one unit ends up seeing another where they only
     *  contribute. Naming the role they hold IN that unit is the truthful answer;
     *  falling through to their platform role labelled a unit they administer nothing
     *  in "Project Admin", which is the overstatement this whole change is about. */
    const roleFromProjectsIn = new Map<string, string>();
    for (const p of projects) {
      const own = heldRole.get(String(p.id));
      const unit = p.workspaceId ? String(p.workspaceId) : null;
      if (!own || !unit || roleFromProjectsIn.has(unit)) continue;
      roleFromProjectsIn.set(unit, own);
    }

    /** The explicit binding on this scope, else the one it inherits reach FROM.
     *
     *  Reach is not always explicit: a Business Unit Admin sees every project in their
     *  unit without holding a per-project binding, so a project falls back to the unit
     *  above it. A unit falls back to what is held inside it. Only when neither exists
     *  does the platform role answer — and by then it is the only thing known. */
    const roleFor = (scopeId: string, parentId?: string | null): string =>
      heldRole.get(scopeId) ??
      (parentId ? heldRole.get(parentId) : undefined) ??
      roleFromProjectsIn.get(scopeId) ??
      role ??
      "contributor";

    const unitBindings: ScopeBinding[] = units.map((u) => ({
      kind: "business_unit",
      scopeId: String(u.id),
      scopeName: u.displayName,
      role: roleFor(String(u.id)),
      parentId: null,
      parentName: null,
      status: "active",
    }));

    const projectBindings: ScopeBinding[] = projects.map((p) => ({
      kind: "project",
      scopeId: String(p.id),
      scopeName: p.name,
      role: roleFor(String(p.id), p.workspaceId ? String(p.workspaceId) : null),
      parentId: p.workspaceId ? String(p.workspaceId) : null,
      parentName: p.workspaceId ? (unitName.get(String(p.workspaceId)) ?? null) : null,
      status: "active",
    }));

    const bindings = [...unitBindings, ...projectBindings];

    const scope: AccessScopeOut = {
      level: isOrgWide ? "organization" : businessUnitIds.length > 0 ? "business_unit" : "project",
      isOrgWide,
      businessUnitIds,
      // ADMINISTERED, NOT MERELY VISIBLE. These decide the "You administer" badge, and
      // they were derived from the caller's one effective role — so a Project Admin was
      // told they administer EVERY project they can see, including one in another unit
      // where they are only a contributor. Now a scope is managed when the caller holds
      // an administering binding ON IT, or on the unit above it. Org-wide is still
      // everything, because that is what org-wide means.
      managedBusinessUnitIds: isOrgWide
        ? businessUnitIds
        : businessUnitIds.filter((id) => ADMIN_ROLES.has(heldRole.get(id) ?? "")),
      projectIds,
      managedProjectIds: isOrgWide
        ? projectIds
        : projects
            .filter((p) => {
              const own = heldRole.get(String(p.id)) ?? "";
              if (ADMIN_ROLES.has(own)) return true;
              const unit = p.workspaceId ? heldRole.get(String(p.workspaceId)) : undefined;
              return ADMIN_ROLES.has(unit ?? "");
            })
            .map((p) => String(p.id)),
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
