import { type NextRequest } from "next/server";

import { getSession } from "@/lib/auth/session";
import { createCustomRole, listCustomRoles } from "@/lib/mock/custom-role-fixtures";
import type { CustomRoleScope } from "@/lib/api/roles";
import type { InvolvementLevel } from "@/lib/schemas/agent-access";
import type { Phase } from "@/lib/schemas/enums";

// DUMMY-DATA SEAM: reads/writes the in-memory fixture store directly. When
// the backend roles service lands, replace both bodies with bffFetch(...).

export async function GET() {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });
  return Response.json(listCustomRoles());
}

export async function POST(req: NextRequest) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });
  const body = (await req.json()) as {
    name: string;
    description?: string;
    permissions: string[];
    agentAccess?: Partial<Record<Phase, InvolvementLevel>>;
    scope: CustomRoleScope;
  };
  const role = createCustomRole(body);
  return Response.json(role, { status: 201 });
}
