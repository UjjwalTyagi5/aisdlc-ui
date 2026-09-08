import { ApiRequestError } from "@/lib/api/client";
import { getSession } from "@/lib/auth/session";
import { hasPermission } from "@/lib/auth/permissions";
import { effectivePlatformRole } from "@/lib/auth/effective-role";
import { bffFetch } from "@/lib/bff/client";
import type { DirectoryEntry, OrgRole } from "@/lib/schemas/user-directory";

/**
 * The org-wide people directory — COMPOSED from three FastAPI reads, not from
 * one endpoint and not from fixtures.
 *
 * This route used to serve `lib/mock/user-directory-fixtures`, which is why the
 * page listed Amara Okafor, Ana Silva, Bruno Alves and a dozen colleagues over a
 * `users` table holding two rows. FastAPI has no `/admin/users` returning this
 * shape, but it does hold every fact the shape needs, spread across:
 *
 *   GET /admin/workspaces              the Business Units
 *   GET /admin/org-members             every person in the tenant
 *   GET /admin/members?workspace_id=…  who holds which role in one unit
 *
 * Composing them HERE is what a BFF is for: the fan-out is bounded by the number
 * of Business Units, and the alternative — the browser making N+2 calls and
 * joining them — ships the same data over a slower wire.
 *
 * ONE FACT IS NOT READABLE, and it is not invented to compensate.
 * `/admin/members` selects `scope_kind = 'business_unit'`, so an ORGANIZATION-
 * scoped binding — which is how `org_admin` is granted — appears in no unit's
 * member list. The viewer's own row is therefore filled from their session
 * (authenticated, backend-issued) and everyone else's `orgRole` is derived from
 * the unit bindings that ARE readable. Someone with no readable binding is
 * reported as an unplaced contributor with `awaitingRole` set, because that is
 * exactly what the database says about them: on the platform, in no unit.
 *
 * BACKLOG: a FastAPI `GET /admin/users` that reads organization-scope bindings
 * too, which would collapse this whole handler into one bffProxy call.
 *
 * SCOPE is unchanged and still `member:manage` — org-wide READ, unit-scoped
 * WRITE ([[bu-admin-reads-org-writes-unit]]). The write endpoints check
 * `canManageBusinessUnit` independently, so an org-wide read never becomes an
 * org-wide edit.
 */
export const dynamic = "force-dynamic";

interface AdminWorkspace {
  id: string;
  name: string;
}

interface AdminOrgMember {
  userId: string;
  email: string | null;
  initials: string;
}

interface AdminMember {
  userId: string;
  name: string | null;
  email: string | null;
  initials: string;
  roles: string[];
}

interface AdminProject {
  id: string;
  name: string;
  workspaceId?: string | null;
}

interface ProjectMember {
  identity?: { id?: string };
  role?: string;
  status?: string;
}

interface DirectoryProjectBinding {
  scope: "project";
  id: string;
  name: string;
  businessUnitId: string | null;
  role: string;
  status: "active" | "invited" | "deactivated";
}

/** A display name from an email local part — the backend stores no `name`. */
function nameFromEmail(email: string | null, userId: string): string {
  const local = email?.split("@")[0];
  if (!local) return userId;
  return local
    .split(/[._\-+]+/)
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

/**
 * The org-level appointment implied by a unit binding.
 *
 * Only `bu_admin` is legible this way — it is the one role whose presence in a
 * unit says something about the org-level decision that put them there. Every
 * other unit role means "works in a unit", which is `contributor`.
 */
function orgRoleFromUnitRoles(roles: string[]): OrgRole {
  return roles.includes("bu_admin") ? "bu_admin" : "contributor";
}

export async function GET() {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });
  if (!hasPermission(session, "member:manage")) {
    return Response.json({ code: "forbidden", message: "forbidden" }, { status: 403 });
  }

  try {
    const [units, people, projectPage] = await Promise.all([
      bffFetch("/admin/workspaces", { session }) as Promise<AdminWorkspace[]>,
      bffFetch("/admin/org-members", { session }) as Promise<AdminOrgMember[]>,
      // PROJECT BINDINGS COME FROM THE PROJECT ROSTERS, because `/admin/members`
      // cannot supply them: it selects `scope_kind = 'business_unit'` by definition —
      // it answers "who is in this unit". So the directory's "Project roles" column
      // had no source at all and rendered an em dash for everyone, including people
      // holding two project bindings.
      (
        bffFetch("/projects?page_size=200", { session }) as Promise<
          { items?: AdminProject[] } | AdminProject[]
        >
      ).catch(() => [] as AdminProject[]),
    ]);
    const projects = Array.isArray(projectPage) ? projectPage : (projectPage.items ?? []);

    // One call per unit. Sequential would make the page's latency a function of
    // how many Business Units the org has, which is the wrong thing to scale on.
    const rosters = await Promise.all(
      units.map(async (unit) => ({
        unit,
        members: (await bffFetch(
          `/admin/members?workspace_id=${encodeURIComponent(unit.id)}`,
          { session },
        )) as AdminMember[],
      })),
    );

    // One call per project, in parallel with each other for the same reason the unit
    // rosters are. Scoped server-side: `/projects` already returns only what this
    // viewer may see, so this cannot widen the directory beyond their reach.
    const projectRosters = await Promise.all(
      projects.map(async (project) => ({
        project,
        members: await (
          bffFetch(`/projects/${encodeURIComponent(project.id)}/members`, {
            session,
          }) as Promise<ProjectMember[]>
        ).catch(
          // DEGRADE PER PROJECT. One unreadable roster must not blank the whole
          // directory — the column is worth less complete than the page is worth at
          // all.
          () => [] as ProjectMember[],
        ),
      })),
    );

    const projectBindingsByUser = new Map<string, DirectoryProjectBinding[]>();
    for (const { project, members } of projectRosters) {
      for (const m of members) {
        const uid = m.identity?.id;
        if (!uid || !m.role) continue;
        const list = projectBindingsByUser.get(uid) ?? [];
        list.push({
          scope: "project",
          id: String(project.id),
          name: project.name,
          // The unit the project sits in — what decides whether the viewer may EDIT
          // this binding, which the table reads straight off it.
          businessUnitId: project.workspaceId ? String(project.workspaceId) : null,
          role: m.role,
          status: m.status === "invited" || m.status === "deactivated" ? m.status : "active",
        });
        projectBindingsByUser.set(uid, list);
      }
    }

    // userId → the units they hold a role in, with the roles held there.
    const bindingsByUser = new Map<
      string,
      Array<{ unit: AdminWorkspace; roles: string[] }>
    >();
    for (const { unit, members } of rosters) {
      for (const member of members) {
        const list = bindingsByUser.get(member.userId) ?? [];
        list.push({ unit, roles: member.roles });
        bindingsByUser.set(member.userId, list);
      }
    }

    const viewerId = session.user.id;
    const viewerRole = effectivePlatformRole(session);

    const directory: DirectoryEntry[] = people.map((person) => {
      const held = bindingsByUser.get(person.userId) ?? [];
      // The first unit is the one the directory calls theirs. With no binding
      // there is no unit, and saying so is the point — that is the gap the
      // "needs a role" queue exists to close.
      const home = held[0];
      const unitRoles = home?.roles ?? [];
      // `contributor` in a unit is the placeholder, not a role: it means the
      // unit's admin has not yet said what this person does.
      const realUnitRole = unitRoles.find((r) => r !== "contributor") ?? null;

      const isViewer = person.userId === viewerId;
      const orgRole: OrgRole = isViewer && viewerRole === "org_admin"
        ? "org_admin"
        : orgRoleFromUnitRoles(unitRoles);

      return {
        userId: person.userId,
        identityId: person.userId,
        displayName: nameFromEmail(person.email, person.userId),
        email: person.email,
        initials: person.initials,
        orgRole,
        unitRole: realUnitRole,
        businessUnitId: home ? home.unit.id : null,
        businessUnitName: home ? home.unit.name : null,
        bindings: [
          ...held.flatMap(({ unit, roles }) =>
            roles.map((role) => ({
              scope: "business_unit" as const,
              id: unit.id,
              name: unit.name,
              businessUnitId: unit.id,
              role,
              status: "active" as const,
            })),
          ),
          ...(projectBindingsByUser.get(person.userId) ?? []),
        ],
        // Placed in a unit but holding only the placeholder, OR on the platform
        // and placed nowhere. Both are somebody's outstanding decision.
        awaitingRole: orgRole !== "org_admin" && realUnitRole === null,
        // Cross-unit loans have no backend representation — see
        // app/api/admin/cross-bu-grants/route.ts. Nobody is a guest until they do.
        isGuest: false,
      };
    });

    directory.sort((a, b) => a.displayName.localeCompare(b.displayName));
    return Response.json(directory);
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
