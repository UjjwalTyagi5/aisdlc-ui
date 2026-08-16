import { type NextRequest } from "next/server";

import { bffProxy } from "@/lib/bff/proxy";

/**
 * Governance requests — proxied to FastAPI `GET/POST /governance-approvals`.
 *
 * The backend now models these as their own thing rather than as a variant of
 * `approval_requests`: sixteen types, seven statuses, a priority, attachments and
 * an append-only timeline. See `backend/migrations/versions/0010_*` for why the
 * two lanes are not one table.
 *
 * EVERY GUARD THAT USED TO RUN HERE IS NOW THE BACKEND'S, and that is the point of
 * the move rather than a side effect. The old handler checked `canRaiseRequest`,
 * `canRaiseType` and the requester's scope against fixture stores — real rules
 * enforced against imaginary data. They are the same rules, applied to bindings:
 *
 *   - an Organization Admin cannot raise one (nothing sits above them to decide it)
 *   - a tier can only raise what it cannot grant itself
 *   - the requester's role and the approver are taken from the session, never the
 *     body — a client that could name its own role would pick the one whose chain
 *     is shortest
 *
 * `workspaceId` is forwarded as the caller's own narrowing choice (the queue's
 * "this unit" filter). It is not the boundary: the backend intersects it with what
 * the viewer may read, and adds back anything they raised themselves wherever it
 * has climbed to.
 */
export const dynamic = "force-dynamic";

export async function GET(req: NextRequest) {
  const workspaceId = req.nextUrl.searchParams.get("workspaceId");
  const qs = workspaceId ? `?workspaceId=${encodeURIComponent(workspaceId)}` : "";
  return bffProxy(`/governance-approvals${qs}`);
}

export async function POST(req: NextRequest) {
  const body: unknown = await req.json();
  return bffProxy("/governance-approvals", { method: "POST", body });
}
