import { type NextRequest } from "next/server";

import { removeProjectMember, updateProjectMemberRole } from "@/lib/mock/project-membership-fixtures";

// DUMMY-DATA SEAM: reads/writes the in-memory fixture store directly.

type Params = Promise<{ id: string; membershipId: string }>;

export async function PATCH(req: NextRequest, { params }: { params: Params }) {
  const { id, membershipId } = await params;
  const body = (await req.json()) as { roleName: string };
  const member = updateProjectMemberRole(id, membershipId, body.roleName);
  if (!member) return Response.json({ code: "not_found" }, { status: 404 });
  return Response.json(member);
}

export async function DELETE(_req: NextRequest, { params }: { params: Params }) {
  const { id, membershipId } = await params;
  removeProjectMember(id, membershipId);
  return new Response(null, { status: 204 });
}
