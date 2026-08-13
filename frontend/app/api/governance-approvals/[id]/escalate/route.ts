import { notImplemented } from "@/lib/bff/not-implemented";

/**
 * Send a request up a tier when its approver hasn't answered.
 *
 * NOT IMPLEMENTED BY THE BACKEND — `approval_requests` records one target role
 * and one terminal decision, with no `escalated` state and no trail of the roles
 * a request has passed through. See app/api/governance-approvals/route.ts.
 *
 * BACKLOG: FastAPI `POST /governance-approvals/{id}/escalate`.
 */
export async function POST() {
  return notImplemented("POST /governance-approvals/{id}/escalate");
}
