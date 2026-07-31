import { type NextRequest } from "next/server";

import { getSession } from "@/lib/auth/session";
import {
  findOrCreateIdentityBySsoSubject,
  removeMembership,
  setMembershipRole,
} from "@/lib/mock/workspace-fixtures";

type Params = Promise<{ id: string; userId: string }>;

// DUMMY-DATA SEAM: mirrors mocks/handlers.ts — see [[msw-dual-runtime-mutation-rule]].
export async function PATCH(req: NextRequest, { params }: { params: Params }) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });
  const { id, userId } = await params;
  const body = (await req.json().catch(() => ({}))) as { roleName?: string };
  if (!body.roleName) {
    return Response.json({ code: "invalid_input", message: "roleName is required" }, { status: 422 });
  }

  const identity = findOrCreateIdentityBySsoSubject(userId);
  setMembershipRole(id, identity.id, body.roleName);
  return Response.json({
    userId: identity.ssoSubject,
    email: identity.email,
    displayName: identity.displayName,
    initials: identity.initials,
    roleName: body.roleName,
    joinedAt: new Date().toISOString(),
  });
}

export async function DELETE(_req: NextRequest, { params }: { params: Params }) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });
  const { id, userId } = await params;

  const identity = findOrCreateIdentityBySsoSubject(userId);
  removeMembership(id, identity.id);
  return new Response(null, { status: 204 });
}
