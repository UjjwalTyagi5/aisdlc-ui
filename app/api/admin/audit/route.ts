import { type NextRequest } from "next/server";

import { bffFetch } from "@/lib/bff/client";
import { ApiRequestError } from "@/lib/api/client";
import { getSession } from "@/lib/auth/session";

/**
 * Org-level audit log BFF route. Does NOT forward X-Workspace-Id — this is an
 * org-wide view. workspace_id can be passed as a query param for workspace filtering.
 */
export async function GET(req: NextRequest) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });

  const search = req.nextUrl.searchParams.toString();
  const path = search ? `/admin/audit?${search}` : "/admin/audit";

  try {
    return Response.json(await bffFetch(path, { session }));
  } catch (e) {
    if (e instanceof ApiRequestError) {
      return Response.json({ code: e.code, message: e.message }, { status: e.status });
    }
    throw e;
  }
}
