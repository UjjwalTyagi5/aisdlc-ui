import { type NextRequest } from "next/server";

import { listMembers, findOrCreateIdentityBySsoSubject, setMembershipRole } from "@/lib/mock/workspace-fixtures";
import { getSession } from "@/lib/auth/session";

// DUMMY-DATA SEAM: mirrors mocks/handlers.ts — see [[msw-dual-runtime-mutation-rule]].
export async function GET(
  _req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });
  const { id } = await params;
  // Transform fixture WorkspaceMember (frontend-first, Identity-rich) →
  // WorkspaceMemberOut (backend-aligned, simpler) so the schema stays in sync.
  const members = listMembers(id).map((m) => ({
    userId: m.identity.ssoSubject,
    email: m.identity.email,
    displayName: m.identity.displayName,
    initials: m.identity.initials,
    roleName: m.role,
    joinedAt: new Date().toISOString(),
  }));
  return Response.json(members);
}

export async function POST(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });
  const { id } = await params;
  const body = (await req.json().catch(() => ({}))) as {
    userId?: string;
    roleName?: string;
    email?: string | null;
    initials?: string;
  };
  if (!body.userId || !body.roleName) {
    return Response.json(
      { code: "validation_error", message: "userId and roleName are required" },
      { status: 422 },
    );
  }

  const identity = findOrCreateIdentityBySsoSubject(body.userId, body.email, body.initials);
  setMembershipRole(id, identity.id, body.roleName);
  return Response.json(
    {
      userId: identity.ssoSubject,
      email: identity.email,
      displayName: identity.displayName,
      initials: identity.initials,
      roleName: body.roleName,
      joinedAt: new Date().toISOString(),
    },
    { status: 201 },
  );
}
