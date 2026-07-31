import { buildOrgOverview } from "@/lib/mock/org-overview-fixtures";
import { getSession } from "@/lib/auth/session";
import { resolveSessionScope, type AccessScope } from "@/lib/auth/access-scope";

// DUMMY-DATA SEAM: composes the existing fixture stores — see
// lib/mock/org-overview-fixtures.ts. Mirrored in mocks/handlers.ts, per
// [[msw-dual-runtime-mutation-rule]].
//
// SCOPE FILTER: this endpoint returns counts as well as rows, and a count is
// just as disclosing — "your organization has 47 people" is a fact a Business
// Unit Admin has no claim on. Everything is therefore computed from the
// caller's allowed set rather than filtered after the fact.
const allowedFor = (scope: AccessScope) =>
  scope.isOrgWide
    ? null
    : { workspaceIds: scope.businessUnitIds, projectIds: scope.projectIds };

export const dynamic = "force-dynamic";

export async function GET() {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });
  return Response.json(buildOrgOverview(allowedFor(resolveSessionScope(session))));
}
