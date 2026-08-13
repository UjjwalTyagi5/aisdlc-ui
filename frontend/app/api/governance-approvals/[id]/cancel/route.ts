import { type NextRequest } from "next/server";

import { bffProxy } from "@/lib/bff/proxy";

/**
 * Withdraw a request you raised — proxied to FastAPI
 * `POST /governance-approvals/{id}/cancel`.
 *
 * The INITIATOR only, enforced in the backend service. An approver who wants it
 * gone rejects it, which records a decision; letting them cancel would let an
 * approver make a request disappear without ever answering it.
 */
export async function POST(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const body: unknown = await req.json().catch(() => ({}));
  return bffProxy(`/governance-approvals/${encodeURIComponent(id)}/cancel`, {
    method: "POST",
    body,
  });
}
