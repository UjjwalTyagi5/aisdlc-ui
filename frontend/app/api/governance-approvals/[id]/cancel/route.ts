import { notImplemented } from "@/lib/bff/not-implemented";

/**
 * Withdraw a request you raised.
 *
 * NOT IMPLEMENTED BY THE BACKEND — `approval_requests.status` has no
 * `cancelled` state, so there is nowhere for this transition to land. See
 * app/api/governance-approvals/route.ts.
 *
 * BACKLOG: FastAPI `POST /governance-approvals/{id}/cancel`.
 */
export async function POST() {
  return notImplemented("POST /governance-approvals/{id}/cancel");
}
