import { type NextRequest } from "next/server";

import { getSession } from "@/lib/auth/session";
import { deleteCustomRole, updateCustomRole } from "@/lib/mock/custom-role-fixtures";
import type { CustomRoleScope } from "@/lib/api/roles";
import type { InvolvementLevel } from "@/lib/schemas/agent-access";
import type { Phase } from "@/lib/schemas/enums";

// DUMMY-DATA SEAM: writes the in-memory fixture store directly. When the
// backend roles service lands, replace the body with bffFetch(...).
export async function PATCH(req: NextRequest, ctx: { params: Promise<{ id: string }> }) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });
  const { id } = await ctx.params;
  const body = (await req.json()) as Partial<{
    name: string;
    description: string | null;
    permissions: string[];
    agentAccess: Partial<Record<Phase, InvolvementLevel>>;
    scope: CustomRoleScope;
  }>;
  const role = updateCustomRole(id, body);
  if (!role) return Response.json({ code: "not_found" }, { status: 404 });
  return Response.json(role);
}

export async function DELETE(_req: NextRequest, ctx: { params: Promise<{ id: string }> }) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });
  const { id } = await ctx.params;
  deleteCustomRole(id);
  return new Response(null, { status: 204 });
}
