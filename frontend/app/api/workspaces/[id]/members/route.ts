import { type NextRequest } from "next/server";

import { bffProxy } from "@/lib/bff/proxy";

/**
 * A Business Unit's roster — proxied to FastAPI
 * `GET/POST /workspaces/{id}/members`.
 *
 * The fixture version had to transform its own `WorkspaceMember` (Identity-rich)
 * into the backend-aligned `WorkspaceMemberOut` on the way out, and stamped
 * `joinedAt` with `new Date()` on every read — so a member's join date changed
 * each time the page was refreshed. FastAPI returns the real shape and the real
 * date.
 *
 * The `canManageBusinessUnit` gate on POST is the backend's. The reason for it is
 * unchanged: the people directory is org-wide, so the WRITE is where the scope has
 * to be enforced ([[bu-admin-reads-org-writes-unit]]).
 */
export async function GET(_req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return bffProxy(`/workspaces/${encodeURIComponent(id)}/members`);
}

export async function POST(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const body = (await req.json().catch(() => ({}))) as {
    userId?: string;
    roleName?: string;
  };
  if (!body.userId || !body.roleName) {
    return Response.json(
      { code: "validation_error", message: "userId and roleName are required" },
      { status: 422 },
    );
  }
  return bffProxy(`/workspaces/${encodeURIComponent(id)}/members`, { method: "POST", body });
}
