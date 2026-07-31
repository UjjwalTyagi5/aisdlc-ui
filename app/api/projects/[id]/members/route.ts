import { type NextRequest } from "next/server";

import { addProjectMember, listProjectMembers } from "@/lib/mock/project-membership-fixtures";

// DUMMY-DATA SEAM: reads/writes the in-memory fixture store directly — this
// is a net-new project-scoped role model with no backend equivalent yet.

export async function GET(_req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return Response.json(listProjectMembers(id));
}

export async function POST(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const body = (await req.json()) as { email: string; displayName?: string; roleName: string };
  if (!body?.email || !body?.roleName) {
    return Response.json({ code: "invalid_input", message: "email and roleName are required" }, {
      status: 422,
    });
  }
  const member = addProjectMember(id, body);
  return Response.json(member, { status: 201 });
}
