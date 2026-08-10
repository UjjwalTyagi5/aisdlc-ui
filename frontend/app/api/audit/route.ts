import { type NextRequest } from "next/server";

import { AUDIT_EVENTS } from "@/mocks/fixtures";
import { getSession } from "@/lib/auth/session";
import { resolveSessionScope } from "@/lib/auth/access-scope";
import { filterByProject } from "@/lib/mock/access-scope";

// DUMMY-DATA SEAM: was an unconditional `bffProxy("/audit")`, which 500s with
// no FastAPI running — see [[dummy-data-seam-pattern]]. Now reads the shared
// AUDIT_EVENTS fixture, matching the MSW handler for the same path so the two
// runtimes agree.
//
// SCOPE FILTER: the audit trail is the most sensitive cross-scope leak — it
// names who did what to which resource. Rows are filtered to the projects the
// viewer may read; organization-level rows (`projectId: null`) are dropped for
// anyone not org-wide, since an unattributable governance event is exactly the
// row that must not cross a boundary.
export async function GET(req: NextRequest) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });

  const scope = resolveSessionScope(session);
  const url = req.nextUrl;
  const projectId = url.searchParams.get("projectId");
  const action = url.searchParams.get("action");

  let rows = filterByProject(scope, AUDIT_EVENTS, (e) => e.projectId);
  if (projectId) rows = rows.filter((e) => e.projectId === projectId);
  if (action) rows = rows.filter((e) => e.action === action);

  const page = Number(url.searchParams.get("page") ?? "1");
  const pageSize = Math.min(Number(url.searchParams.get("pageSize") ?? "20"), 200);
  const start = (page - 1) * pageSize;

  return Response.json({
    items: rows.slice(start, start + pageSize),
    pagination: { page, pageSize, total: rows.length },
  });
}
