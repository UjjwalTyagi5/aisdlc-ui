import { type NextRequest } from "next/server";

import { getSession } from "@/lib/auth/session";
import { resolveSessionScope } from "@/lib/auth/access-scope";
import { effectivePlatformRole } from "@/lib/auth/effective-role";
import {
  connectorGrantsForWorkspace,
  connectorGrantsForWorkspaces,
  listConnectorGrants,
  setBuConnectorGrants,
  setConnectorGrants,
} from "@/lib/mock/connector-grants";
import type { ConnectorGrant } from "@/lib/schemas/connector";

// DUMMY-DATA SEAM: mutates the shared grant store directly. Mirrored in
// mocks/handlers.ts — see [[msw-dual-runtime-mutation-rule]].
export const dynamic = "force-dynamic";

/**
 * Which connector kinds are permitted.
 *
 *  - a `workspaceId`  — the grants that reach that unit. Kept as grants rather
 *    than bare kinds so the UI can distinguish "every unit has this" from "you
 *    were given this".
 *  - no `workspaceId`, org-wide viewer — the whole policy, including which
 *    units each specific grant names, for the Org Admin who wrote it.
 *  - no `workspaceId`, bounded viewer — the union across the units they're
 *    bound to, with the unit lists stripped. Someone in two units should not
 *    have to name one to find out what they may connect, and must not learn
 *    which OTHER units a grant reaches.
 */
export async function GET(req: NextRequest) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });

  const workspaceId = req.nextUrl.searchParams.get("workspaceId");
  const scope = resolveSessionScope(session);

  if (workspaceId) {
    if (!scope.isOrgWide && !scope.businessUnitIds.includes(String(workspaceId))) {
      return Response.json({ code: "not_found" }, { status: 404 });
    }
    return Response.json(connectorGrantsForWorkspace(workspaceId));
  }

  if (!scope.isOrgWide) {
    return Response.json(connectorGrantsForWorkspaces(scope.businessUnitIds));
  }
  return Response.json(listConnectorGrants());
}

/**
 * Set the policy. With a `workspaceId` this grants that one unit a set of
 * kinds (Business Unit creation / management); without one it replaces the
 * whole list (the Integrations page's per-connector visibility control).
 * Either way it is the Organization Admin's decision alone — a BU Admin who
 * could edit their own grants could permit themselves anything.
 */
export async function PUT(req: NextRequest) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });
  if (effectivePlatformRole(session) !== "org_admin") {
    return Response.json(
      { code: "forbidden", message: "Only an Organization Admin can change connector grants." },
      { status: 403 },
    );
  }

  const workspaceId = req.nextUrl.searchParams.get("workspaceId");
  const body = (await req.json()) as { grants?: ConnectorGrant[]; kinds?: string[] };

  if (workspaceId) {
    return Response.json(setBuConnectorGrants(workspaceId, body.kinds ?? []));
  }
  return Response.json(setConnectorGrants(body.grants ?? []));
}
