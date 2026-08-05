import { type NextRequest } from "next/server";

import { getSession } from "@/lib/auth/session";
import { resolveSessionScope } from "@/lib/auth/access-scope";
import { effectivePlatformRole } from "@/lib/auth/effective-role";
import { listIntegrationAccess, revokeProjectIntegration } from "@/lib/mock/integration-access";
import { grantConnectorToUnit, revokeConnectorGrant } from "@/lib/mock/connector-grants";
import { grantMcpToUnit, revokeMcpGrant } from "@/lib/mock/mcp-fixtures";
import { PROJECTS } from "@/mocks/fixtures";

// DUMMY-DATA SEAM: reads and mutates the shared grant + project stores.
// Mirrored in mocks/handlers.ts — see [[msw-dual-runtime-mutation-rule]].
export const dynamic = "force-dynamic";

/** Every integration, with the units that may use it and the projects that do. */
export async function GET() {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });

  const scope = resolveSessionScope(session);
  return Response.json(
    listIntegrationAccess(scope.isOrgWide ? null : scope.businessUnitIds),
  );
}

/**
 * Give a Business Unit access to one integration. Organization Admin only.
 *
 * The counterpart to revoking at unit level. Access is normally decided when a
 * unit is created; this is how a unit gains something afterwards, which was
 * otherwise unreachable — the screen could take access away and never give it.
 */
export async function POST(req: NextRequest) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });
  if (effectivePlatformRole(session) !== "org_admin") {
    return Response.json(
      { code: "forbidden", message: "Only an Organization Admin grants integration access." },
      { status: 403 },
    );
  }

  const p = req.nextUrl.searchParams;
  const kind = p.get("kind") === "mcp" ? "mcp" : "connector";
  const targetId = p.get("id");
  if (!targetId) {
    return Response.json({ code: "bad_request", message: "Name the integration." }, { status: 400 });
  }

  // BUSINESS UNIT LEVEL ONLY. Granting is how far the organization's reach
  // decision goes; whether one of a unit's projects switches the integration on
  // is that project's wiring, done on Settings → Tools per stage. Revoking DOES
  // reach a project — an admin taking something away has to be able to stop one
  // team without punishing the rest of the unit.
  const workspaceId = p.get("workspaceId");
  if (!workspaceId) {
    return Response.json(
      { code: "bad_request", message: "Name the business unit." },
      { status: 400 },
    );
  }
  const units =
    kind === "mcp" ? grantMcpToUnit(targetId, workspaceId) : grantConnectorToUnit(targetId, workspaceId);
  return Response.json({ ok: true, remainingUnits: units });
}

/**
 * Revoke access, at one of two levels.
 *
 *   ?level=unit&workspaceId=…    the unit loses the integration entirely.
 *                                Organization Admin only — a Business Unit
 *                                Admin who could revoke their own grant could
 *                                also restore it, which makes the grant theirs.
 *   ?level=project&projectId=…   the project stops using it. Either admin
 *                                tier, bounded to their own scope.
 *
 * Both are idempotent: revoking what is already gone returns ok with
 * `changed: false` rather than a 404, because the caller's intent is satisfied
 * either way and a 404 here reads as "wrong id".
 */
export async function DELETE(req: NextRequest) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });

  const p = req.nextUrl.searchParams;
  const kind = p.get("kind") === "mcp" ? "mcp" : "connector";
  const targetId = p.get("id");
  const level = p.get("level");
  if (!targetId) {
    return Response.json({ code: "bad_request", message: "Name the integration." }, { status: 400 });
  }

  const role = effectivePlatformRole(session);
  const scope = resolveSessionScope(session);

  if (level === "unit") {
    const workspaceId = p.get("workspaceId");
    if (!workspaceId) {
      return Response.json({ code: "bad_request", message: "Name the unit." }, { status: 400 });
    }
    if (role !== "org_admin") {
      return Response.json(
        {
          code: "forbidden",
          message: "Only an Organization Admin can take an integration away from a business unit.",
        },
        { status: 403 },
      );
    }
    const left =
      kind === "mcp" ? revokeMcpGrant(targetId, workspaceId) : revokeConnectorGrant(targetId, workspaceId);
    return Response.json({ ok: true, remainingUnits: left });
  }

  if (level === "project") {
    const projectId = p.get("projectId");
    if (!projectId) {
      return Response.json({ code: "bad_request", message: "Name the project." }, { status: 400 });
    }
    if (role !== "org_admin" && role !== "bu_admin") {
      return Response.json(
        { code: "forbidden", message: "Only an admin tier can revoke a project's integration." },
        { status: 403 },
      );
    }
    // Bounded to the units they administer — a BU Admin must not reach into a
    // sibling unit's project.
    const project = PROJECTS.find((x) => String(x.id) === projectId);
    if (!project) return Response.json({ code: "not_found" }, { status: 404 });
    if (!scope.isOrgWide && !scope.businessUnitIds.includes(String(project.workspaceId))) {
      return Response.json({ code: "not_found" }, { status: 404 });
    }

    const changed = revokeProjectIntegration(projectId, kind, targetId);
    return Response.json({ ok: true, changed });
  }

  return Response.json(
    { code: "bad_request", message: "level must be 'unit' or 'project'." },
    { status: 400 },
  );
}
