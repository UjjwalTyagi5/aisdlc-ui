import { type NextRequest } from "next/server";

import { ApiRequestError } from "@/lib/api/client";
import { getSession } from "@/lib/auth/session";
import { bffFetch } from "@/lib/bff/client";
import type { CustomRoleScope } from "@/lib/api/roles";
import type { InvolvementLevel } from "@/lib/schemas/agent-access";
import type { Phase } from "@/lib/schemas/enums";

/**
 * Custom roles — read and created against FastAPI `/admin/custom-roles`.
 *
 * A rename adapter, not a proxy, because the two sides spell ownership
 * differently: FastAPI returns `scopeKind` + `scopeId`, this contract wants
 * `scope` + `businessUnitId`. Renaming a field is not adapting a shape — every
 * value below comes from the row as stored.
 *
 * `agentAccess` IS PERSISTED (migration 0060). It used to be accepted here and
 * dropped on the floor, so a role composed with Strategy=Primary came back reading
 * "No access" on every phase — the form reported success and the answer was gone.
 *
 * Ownership is resolved by the backend from the caller, exactly as the fixture
 * version resolved it from the session and for the same reason: an org-wide role
 * is assignable in every unit, so defining one is not a Business Unit Admin's to
 * do. The two creation endpoints are how FastAPI expresses that — the org one
 * checks `is_org_wide`, the unit one checks `assert_can_write_workspace`.
 *
 */
export const dynamic = "force-dynamic";

export interface BackendCustomRole {
  id: string;
  name: string;
  description: string | null;
  permissions: string[];
  scopeKind: string;
  scopeId: string | null;
  createdBy: string | null;
  agentAccess: Partial<Record<Phase, InvolvementLevel>> | null;
}

/**
 * Exported because the [id] route needs the IDENTICAL translation. It used to
 * proxy PATCH straight through, so an edit answered with FastAPI's `scopeKind` /
 * `scopeId` while the client validates `scope` / `businessUnitId` — every save
 * failed on "response did not match schema" after the write had already landed.
 */
export function toCustomRole(row: BackendCustomRole) {
  return {
    id: row.id,
    name: row.name,
    description: row.description,
    permissions: row.permissions,
    scope: (row.scopeKind === "business_unit" ? "business_unit" : "organization") as CustomRoleScope,
    businessUnitId: row.scopeKind === "business_unit" ? row.scopeId : null,
    agentAccess: row.agentAccess ?? undefined,
  };
}

/**
 * The full list, to anyone signed in. Reading what another unit's "Junior Dev"
 * grants is the same disclosure the people directory already makes, and hiding it
 * would leave an unfamiliar role name on a colleague's row unexplainable.
 * Ownership governs WRITING.
 */
export async function GET() {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });

  try {
    const rows = (await bffFetch("/admin/custom-roles", { session })) as BackendCustomRole[];
    return Response.json(rows.map(toCustomRole));
  } catch (err) {
    if (err instanceof ApiRequestError) {
      return Response.json(
        err.details ?? { code: err.code, message: err.message },
        { status: err.status },
      );
    }
    throw err;
  }
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
    businessUnitId?: string | null;
  };

  // The permission gate is the backend's — `role:manage` plus, for the org-wide
  // endpoint, org-wide authority on top of it.
  const path =
    body.scope === "business_unit" && body.businessUnitId
      ? `/admin/custom-roles/business-unit/${encodeURIComponent(body.businessUnitId)}`
      : "/admin/custom-roles";

  try {
    const created = (await bffFetch(path, {
      session,
      method: "POST",
      body: {
        name: body.name,
        description: body.description ?? null,
        permissions: body.permissions ?? [],
        agentAccess: body.agentAccess ?? null,
      },
    })) as BackendCustomRole;
    return Response.json(toCustomRole(created), { status: 201 });
  } catch (err) {
    if (err instanceof ApiRequestError) {
      return Response.json(
        err.details ?? { code: err.code, message: err.message },
        { status: err.status },
      );
    }
    throw err;
  }
}
