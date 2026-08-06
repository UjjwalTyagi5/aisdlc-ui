import { type NextRequest } from "next/server";

import { getSession } from "@/lib/auth/session";
import { hasPermission } from "@/lib/auth/permissions";
import { resolveSessionScope } from "@/lib/auth/access-scope";
import {
  createCustomRole,
  listCustomRoles,
  resolveRoleOwner,
} from "@/lib/mock/custom-role-fixtures";
import type { CustomRoleScope } from "@/lib/api/roles";
import type { InvolvementLevel } from "@/lib/schemas/agent-access";
import type { Phase } from "@/lib/schemas/enums";

// DUMMY-DATA SEAM: reads/writes the in-memory fixture store directly. When
// the backend roles service lands, replace both bodies with bffFetch(...).

// The full list, to anyone signed in. Reading what another unit's "Junior Dev"
// grants is the same disclosure the people directory already makes, and hiding
// it would leave an unfamiliar role name on a colleague's row unexplainable.
// Ownership governs WRITING — see `canWriteCustomRole`.
export async function GET() {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });
  return Response.json(listCustomRoles());
}

export async function POST(req: NextRequest) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });
  if (!hasPermission(session, "role:manage")) {
    return Response.json({ code: "forbidden", message: "forbidden" }, { status: 403 });
  }

  const body = (await req.json()) as {
    name: string;
    description?: string;
    permissions: string[];
    agentAccess?: Partial<Record<Phase, InvolvementLevel>>;
    scope: CustomRoleScope;
    businessUnitId?: string | null;
  };

  // Ownership is resolved from the session, not read off the body — see
  // `resolveRoleOwner`.
  const owner = resolveRoleOwner(resolveSessionScope(session), body.businessUnitId);
  if ("error" in owner) {
    return Response.json({ code: "forbidden", message: owner.error }, { status: 403 });
  }

  const role = createCustomRole({ ...body, businessUnitId: owner.businessUnitId });
  return Response.json(role, { status: 201 });
}
