import { getSession } from "@/lib/auth/session";
import { notImplemented } from "@/lib/bff/not-implemented";
import { ROLE_ORDER, ROLE_META } from "@/lib/roles";
import { ROLE_PERMISSIONS } from "@/lib/auth/role-permissions";

/**
 * Every built-in role with the permissions it holds.
 *
 * READ IS REAL AND STAYS. A built-in role's permission set is shipped reference
 * data — the same for every tenant, defined in `lib/auth/role-permissions.ts`,
 * mirroring the backend's own `ROLE_PERMISSIONS`. Nothing about it was ever
 * fetched, so nothing about it was ever fake, and answering "what can a Security
 * Engineer do" from the catalogue is correct rather than a stand-in.
 *
 * Readable by anyone signed in, deliberately: "what can this role do" is the
 * question a person asks about their OWN role, and answering it only for
 * administrators makes the permission model something you have to be told rather
 * than look up.
 *
 * THE OVERRIDES ARE GONE. `lib/mock/role-permission-overrides` let an admin
 * retune a built-in role and stored the result in memory, so `isOverridden`
 * reported edits that existed for one server process. FastAPI has no
 * role-permission override table, so every role now reports its shipped default
 * and `isOverridden` is false — which is the true state of every tenant.
 *
 * BACKLOG: FastAPI `GET/PUT/DELETE /admin/role-permissions`.
 */
export const dynamic = "force-dynamic";

export async function GET() {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });

  return Response.json(
    ROLE_ORDER.map((role) => {
      const permissions = [...(ROLE_PERMISSIONS[role] ?? [])];
      return {
        role,
        label: ROLE_META[role].label,
        tier: ROLE_META[role].tier,
        permissions,
        defaults: permissions,
        isOverridden: false,
      };
    }),
  );
}

export async function PUT() {
  return notImplemented("PUT /admin/role-permissions");
}

export async function DELETE() {
  return notImplemented("DELETE /admin/role-permissions");
}
