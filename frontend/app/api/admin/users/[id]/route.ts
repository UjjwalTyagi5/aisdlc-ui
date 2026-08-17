import { type NextRequest } from "next/server";

import { ApiRequestError } from "@/lib/api/client";
import { getSession } from "@/lib/auth/session";
import { hasPermission } from "@/lib/auth/permissions";
import { buildRoleSummary, type CustomRoleLike } from "@/lib/auth/role-summary";
import { bffFetch } from "@/lib/bff/client";
import type { UserDetail, UserDetailBinding } from "@/lib/schemas/user-directory";

/**
 * One person, with every scope they belong to and what each role they hold
 * actually grants — COMPOSED from FastAPI reads, as the directory index is.
 * See app/api/admin/users/route.ts for why composition rather than one proxy.
 *
 * PROJECT BINDINGS ARE ALWAYS EMPTY, and that is a fact about the backend
 * rather than about this person: FastAPI stores project-scope rows in
 * `role_bindings` but exposes no endpoint that lists them, so there is nothing
 * to read. Reporting an empty list is the truthful answer available;
 * `lib/mock/project-membership-fixtures` filled it before, which is why a
 * database with zero projects produced people staffed onto four of them.
 *
 * `roleSummaries` is NOT affected — a role's permissions and agent access are
 * shipped reference data (`lib/auth/role-summary.ts`), true independently of
 * any database, and resolving them here invents nothing.
 *
 * BACKLOG: FastAPI `GET /admin/users/{id}`, including project-scope bindings.
 */
export const dynamic = "force-dynamic";

interface AdminWorkspace {
  id: string;
  name: string;
}

interface AdminMember {
  userId: string;
  name: string | null;
  email: string | null;
  initials: string;
  roles: string[];
}

interface AdminOrgMember {
  userId: string;
  email: string | null;
  initials: string;
}

function nameFromEmail(email: string | null, userId: string): string {
  const local = email?.split("@")[0];
  if (!local) return userId;
  return local
    .split(/[._\-+]+/)
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

export async function GET(_req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });

  const { id } = await params;

  try {
    const [units, people, customRoles] = await Promise.all([
      bffFetch("/admin/workspaces", { session }) as Promise<AdminWorkspace[]>,
      bffFetch("/admin/org-members", { session }) as Promise<AdminOrgMember[]>,
      // A custom role's permissions are the tenant's own definition, so unlike
      // the built-in catalogue they have to be read rather than known.
      (bffFetch("/admin/custom-roles", { session }) as Promise<CustomRoleLike[]>).catch(
        () => [] as CustomRoleLike[],
      ),
    ]);

    const person = people.find((p) => p.userId === id);
    if (!person) return Response.json({ code: "not_found" }, { status: 404 });

    const rosters = await Promise.all(
      units.map(async (unit) => ({
        unit,
        members: (await bffFetch(
          `/admin/members?workspace_id=${encodeURIComponent(unit.id)}`,
          { session },
        )) as AdminMember[],
      })),
    );

    const workspaceBindings: UserDetailBinding[] = rosters.flatMap(({ unit, members }) => {
      const member = members.find((m) => m.userId === id);
      if (!member) return [];
      return member.roles.map((role) => ({
        scope: "workspace" as const,
        id: unit.id,
        name: unit.name,
        parentName: null,
        role,
        status: "active" as const,
      }));
    });

    const distinctRoles = [...new Set(workspaceBindings.map((b) => b.role))];

    const detail: UserDetail = {
      userId: person.userId,
      displayName: nameFromEmail(person.email, person.userId),
      email: person.email,
      initials: person.initials,
      workspaceBindings,
      projectBindings: [],
      roleSummaries: distinctRoles.map((role) => buildRoleSummary(role, customRoles)),
    };
    return Response.json(detail);
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

/**
 * Change someone's org-level appointment — which unit they belong to and
 * whether they run it or work in it.
 *
 * Written through FastAPI `POST/DELETE /admin/assignments`, which is a real
 * grant against `role_bindings`. The previous implementation mutated
 * `lib/mock/onboarding`, so the toast said "appointment changed" and the
 * database never heard about it.
 *
 * Revoke-then-grant, in that order: an appointment is a MOVE, and granting
 * first would briefly leave someone holding a role in two units — which, for
 * `bu_admin`, is authority over a unit nobody meant to give them.
 */
export async function PATCH(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });
  if (!hasPermission(session, "admin:*")) {
    return Response.json(
      { code: "forbidden", message: "Appointments are an Organization Admin action." },
      { status: 403 },
    );
  }

  const { id } = await params;
  const body = (await req.json().catch(() => ({}))) as {
    role?: string;
    workspaceId?: string | null;
  };
  if (!body.role) {
    return Response.json(
      { code: "invalid_input", message: "Name the appointment." },
      { status: 422 },
    );
  }
  if (!body.workspaceId) {
    return Response.json(
      { code: "invalid_input", message: "Name the business unit to place them in." },
      { status: 422 },
    );
  }

  try {
    const units = (await bffFetch("/admin/workspaces", { session })) as AdminWorkspace[];
    const rosters = await Promise.all(
      units.map(async (unit) => ({
        unit,
        members: (await bffFetch(
          `/admin/members?workspace_id=${encodeURIComponent(unit.id)}`,
          { session },
        )) as AdminMember[],
      })),
    );

    for (const { unit, members } of rosters) {
      const held = members.find((m) => m.userId === id);
      if (!held) continue;
      for (const role of held.roles) {
        if (unit.id === body.workspaceId && role === body.role) continue;
        await bffFetch("/admin/assignments", {
          session,
          method: "DELETE",
          body: { user_id: id, workspace_id: unit.id, role_name: role },
        });
      }
    }

    await bffFetch("/admin/assignments", {
      session,
      method: "POST",
      body: { user_id: id, workspace_id: body.workspaceId, role_name: body.role },
    });

    return Response.json({ ok: true });
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
