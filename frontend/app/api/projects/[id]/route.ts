import { type NextRequest } from "next/server";

import { bffProxy } from "@/lib/bff/proxy";

/**
 * One project — proxied to FastAPI `GET/PATCH /projects/{id}`.
 *
 * A critical-path route: every one of the project sub-pages loads from this
 * endpoint, and it was reading the shared fixture PROJECTS array, so all of them
 * rendered a project the database does not have.
 *
 * The scope checks are the backend's now — including the shape of the refusal,
 * which matters here more than usual. `/projects/{id}` is directly guessable, so
 * hiding a project from every list is only half the boundary; an unauthorized id
 * must answer 404 rather than 403, because a 403 confirms the id exists and that
 * is itself the cross-project fact being withheld.
 *
 * The manage-vs-read split on PATCH goes with them. It was a real distinction —
 * a contributor who can open a project must not be able to declare it completed
 * or move its budget — and FastAPI draws it against real bindings, which also
 * fixes the case the fixture version had to special-case by hand: a Business Unit
 * Admin administers the projects in their unit without holding a per-project
 * binding.
 */
export async function GET(_req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return bffProxy(`/projects/${encodeURIComponent(id)}`);
}

export async function PATCH(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const body: unknown = await req.json();
  return bffProxy(`/projects/${encodeURIComponent(id)}`, { method: "PATCH", body });
}
