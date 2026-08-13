import { type NextRequest } from "next/server";

import { bffProxy } from "@/lib/bff/proxy";

/**
 * Send a request up a tier — proxied to FastAPI
 * `POST /governance-approvals/{id}/escalate`.
 *
 * Open to the current approver AND to the initiator: the person waiting is the one
 * who knows it has stalled, and a request that can only be escalated by the
 * approver who is ignoring it will never move.
 *
 * Refused with `CANNOT_ESCALATE` at the ceiling — which is the Organization Admin
 * for an admin tier, and the requester's own Project Admin for a contributor. A
 * contributor's ask about one project climbing to an Org Admin would route around
 * the person accountable for it rather than through them.
 */
export async function POST(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const body: unknown = await req.json().catch(() => ({}));
  return bffProxy(`/governance-approvals/${encodeURIComponent(id)}/escalate`, {
    method: "POST",
    body,
  });
}
