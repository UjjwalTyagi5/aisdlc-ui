import { notImplemented } from "@/lib/bff/not-implemented";

/**
 * Approve or reject a governance request.
 *
 * NOT IMPLEMENTED BY THE BACKEND — see app/api/governance-approvals/route.ts
 * for why FastAPI's `/approvals/{id}/approve|reject` is not this endpoint.
 *
 * This one carried more than a status change: on approval it also activated the
 * project, model provider, cross-BU grant or agent-profile version the request
 * was ABOUT, by mutating the fixture stores in place. None of those side effects
 * has a backend equivalent either, so faking the decision would leave a request
 * marked approved and the thing it approved untouched — the worst of the three
 * possible outcomes.
 *
 * BACKLOG: FastAPI `POST /governance-approvals/{id}/decide`, including the
 * apply-on-approve side effects.
 */
export async function POST() {
  return notImplemented("POST /governance-approvals/{id}/decide");
}
