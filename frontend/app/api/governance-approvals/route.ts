import { emptyList, notImplemented } from "@/lib/bff/not-implemented";

/**
 * Governance requests — the Requests & Approvals queue.
 *
 * NOT IMPLEMENTED BY THE BACKEND, despite appearances. FastAPI does have
 * `GET/POST /approvals/requests` over an `approval_requests` table, and it is
 * NOT this: the two disagree on nearly every field the queue renders.
 *
 *   this queue needs        approval_requests has
 *   ─────────────────────   ──────────────────────────────────────────────
 *   16 request types        request_type ∈ {standard, specialist_required}
 *   7 lifecycle statuses    status without submitted/escalated/cancelled
 *   workspace + project     scope_kind/scope_id, unresolved to a name
 *   priority                —
 *   attachments             —
 *   a timeline of events    decided_by/decided_at, one terminal decision
 *   requestedByRole         —
 *
 * Adapting one to the other means inventing a type, a priority and a timeline
 * per row, which is the fabrication this sweep exists to remove — a queue that
 * showed every real request as "Other · normal priority · no history" would be
 * lying more quietly, not less. So: empty until the backend models governance
 * requests as its own thing.
 *
 * This is where the dashboard's "Approvals pending: 7" came from. The table
 * holds zero rows; all seven were `lib/mock/governance-approval-fixtures`.
 *
 * BACKLOG: FastAPI `GET/POST /governance-approvals` carrying the fields above,
 * plus the decide / cancel / escalate transitions in the sibling routes.
 */
export const dynamic = "force-dynamic";

export async function GET() {
  return emptyList();
}

export async function POST() {
  return notImplemented("POST /governance-approvals");
}
