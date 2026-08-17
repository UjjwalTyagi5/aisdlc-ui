import { type NextRequest } from "next/server";

import { bffProxy } from "@/lib/bff/proxy";
import type { ProjectCreateInput } from "@/lib/schemas/project";

/**
 * Projects list + create — proxied to FastAPI `GET/POST /projects`.
 *
 * No fixtures and no scope filtering here any more. Both were doing the
 * backend's job badly: `canReadProject` re-implemented access-scope in the
 * browser tier against an in-memory array, while the real filter is row-level
 * security keyed on the tenant GUC, and `canCreateProject` guarded a POST the
 * backend already gates on `project:create`. A check that runs in front of
 * the real check can only drift from it — and it had: the backend asked for
 * `workspace:manage`, which a Project Admin does not hold, so the button
 * `canCreateProject` showed them led to a 403.
 *
 * Query params are renamed rather than passed through: the frontend speaks
 * `pageSize`, FastAPI speaks `page_size`. Forwarding verbatim silently gave the
 * backend's default page size instead of the one the caller asked for.
 */
function backendQuery(req: NextRequest): string {
  const from = req.nextUrl.searchParams;
  const to = new URLSearchParams();
  to.set("page", from.get("page") ?? "1");
  to.set("page_size", from.get("pageSize") ?? "12");
  // `archived` is a real tri-state: absent means "not archived" (the default
  // list), so only forward it when the caller was explicit.
  const archived = from.get("archived");
  if (archived !== null) to.set("archived", archived);
  const search = from.get("search");
  if (search) to.set("search", search);
  return to.toString();
}

export async function GET(req: NextRequest) {
  return bffProxy(`/projects?${backendQuery(req)}`);
}

export async function POST(req: NextRequest) {
  const body = (await req.json()) as ProjectCreateInput;
  if (!body?.name || !body?.workspaceId) {
    return Response.json(
      { code: "invalid_input", message: "name and workspaceId are required" },
      { status: 422 },
    );
  }
  return bffProxy("/projects", { method: "POST", body });
}
