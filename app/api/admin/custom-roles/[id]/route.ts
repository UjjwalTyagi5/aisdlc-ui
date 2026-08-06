import { type NextRequest } from "next/server";

import { getSession } from "@/lib/auth/session";
import { resolveSessionScope } from "@/lib/auth/access-scope";
import {
  canWriteCustomRole,
  deleteCustomRole,
  getCustomRole,
  updateCustomRole,
} from "@/lib/mock/custom-role-fixtures";
import type { CustomRoleScope } from "@/lib/api/roles";
import type { InvolvementLevel } from "@/lib/schemas/agent-access";
import type { Phase } from "@/lib/schemas/enums";

// DUMMY-DATA SEAM: writes the in-memory fixture store directly. When the
// backend roles service lands, replace the body with bffFetch(...).
//
// A role belongs to the unit that defined it. A Business Unit Admin editing
// another unit's role — or the org-wide one every unit assigns — would change
// what people outside their authority are allowed to do, which is the exact
// escalation the ownership field exists to prevent.
export async function PATCH(req: NextRequest, ctx: { params: Promise<{ id: string }> }) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });
  const { id } = await ctx.params;

  const existing = getCustomRole(id);
  if (!existing) return Response.json({ code: "not_found" }, { status: 404 });
  if (!canWriteCustomRole(resolveSessionScope(session), existing)) {
    return Response.json(
      { code: "forbidden", message: "This role belongs to another business unit." },
      { status: 403 },
    );
  }

  const body = (await req.json()) as Partial<{
    name: string;
    description: string | null;
    permissions: string[];
    agentAccess: Partial<Record<Phase, InvolvementLevel>>;
    scope: CustomRoleScope;
  }>;
  // `businessUnitId` is deliberately not patchable: moving a role between units
  // would re-home every assignment made from it, silently.
  const role = updateCustomRole(id, body);
  if (!role) return Response.json({ code: "not_found" }, { status: 404 });
  return Response.json(role);
}

export async function DELETE(_req: NextRequest, ctx: { params: Promise<{ id: string }> }) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });
  const { id } = await ctx.params;

  const existing = getCustomRole(id);
  if (!existing) return new Response(null, { status: 204 });
  if (!canWriteCustomRole(resolveSessionScope(session), existing)) {
    return Response.json(
      { code: "forbidden", message: "This role belongs to another business unit." },
      { status: 403 },
    );
  }

  deleteCustomRole(id);
  return new Response(null, { status: 204 });
}
