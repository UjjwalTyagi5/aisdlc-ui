import { type NextRequest } from "next/server";

import { createMembership, findOrCreateIdentity, getWorkspace } from "@/lib/mock/workspace-fixtures";
import { addAccessMember } from "@/lib/mock/access-fixtures";

// DUMMY-DATA SEAM: reads/writes the in-memory fixture store directly. Handles
// org- and Business-Unit-scope onboarding (org_admin inviting into either
// tier — an org-level appointment is just a membership whose role is
// "org_admin", still recorded against a workspace since Membership has no
// org-only variant). Project scope is a separate endpoint:
// POST /api/projects/[id]/members.
export async function POST(req: NextRequest) {
  const body = (await req.json()) as {
    email: string;
    displayName?: string;
    workspaceId: string;
    role: string;
  };
  if (!body?.email || !body?.workspaceId || !body?.role) {
    return Response.json(
      { code: "invalid_input", message: "email, workspaceId and role are required" },
      { status: 422 },
    );
  }
  if (!getWorkspace(body.workspaceId)) {
    return Response.json({ code: "not_found", message: "Unknown workspace" }, { status: 404 });
  }

  const identity = findOrCreateIdentity(body.email, body.displayName);
  const membership = createMembership(body.workspaceId, identity.id, body.role);
  // Keep the Roles & Access screen's separate member list (mocks/handlers.ts's
  // ACCESS_MEMBERS) in sync — it doesn't read from workspace-fixtures.ts.
  addAccessMember(
    body.workspaceId,
    { userId: identity.ssoSubject, name: identity.displayName, email: identity.email },
    body.role,
  );

  return Response.json(
    {
      identityId: identity.id,
      email: identity.email,
      displayName: identity.displayName,
      initials: identity.initials,
      workspaceId: membership.workspaceId,
      role: membership.role,
      membershipStatus: "invited",
    },
    { status: 201 },
  );
}
