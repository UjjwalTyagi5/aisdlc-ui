import { type NextRequest } from "next/server";

import { getSession } from "@/lib/auth/session";
import { effectivePlatformRole } from "@/lib/auth/effective-role";
import {
  listRolePermissions,
  resetRolePermissions,
  setRolePermissions,
} from "@/lib/mock/role-permission-overrides";
import type { PlatformRole } from "@/lib/roles";

// DUMMY-DATA SEAM: mutates the in-memory override store. Mirrored in
// mocks/handlers.ts — see [[msw-dual-runtime-mutation-rule]].
export const dynamic = "force-dynamic";

/**
 * Every built-in role with the permissions it holds, the defaults it shipped
 * with, and whether an admin has changed it.
 *
 * Readable by anyone signed in. "What can this role do" is the question a
 * person asks about their OWN role, and answering it only for administrators
 * makes the permission model something you have to be told rather than look
 * up.
 */
export async function GET() {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });
  return Response.json(listRolePermissions());
}

/** Change a built-in role's permissions, or reset it. Organization Admin only. */
export async function PUT(req: NextRequest) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });
  if (effectivePlatformRole(session) !== "org_admin") {
    return Response.json(
      { code: "forbidden", message: "Only an Organization Admin can change a role." },
      { status: 403 },
    );
  }

  const body = (await req.json()) as {
    role?: string;
    permissions?: string[];
    reset?: boolean;
  };
  if (!body.role) {
    return Response.json({ code: "bad_request", message: "Name the role." }, { status: 400 });
  }

  try {
    const row = body.reset
      ? resetRolePermissions(body.role as PlatformRole)
      : setRolePermissions(body.role as PlatformRole, body.permissions ?? []);
    return Response.json(row);
  } catch (e) {
    return Response.json(
      { code: "forbidden", message: e instanceof Error ? e.message : "Cannot change that role." },
      { status: 403 },
    );
  }
}
