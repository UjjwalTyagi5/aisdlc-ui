import { type NextRequest } from "next/server";

import { bffProxy } from "@/lib/bff/proxy";

/**
 * Approve or reject — proxied to FastAPI
 * `POST /governance-approvals/{id}/decide`.
 *
 * The decision and its CONSEQUENCE happen in one transaction there: approving a
 * budget request moves the cap, approving an archive archives the project,
 * approving an agent-default proposal publishes that draft version. If the
 * consequence cannot be applied the decision is refused rather than recorded —
 * a request marked approved over a budget that never moved is the failure mode
 * most likely to go unnoticed, because everyone believes it was handled.
 *
 * Two refusals are worth recognising in the UI by their `code`:
 *   SELF_APPROVAL_BLOCKED   400 — you raised this; it escalates instead
 *   NOT_CURRENT_APPROVER    403 — it is waiting on a different role
 *
 * The first is checked BEFORE "already closed" on purpose, so a second attempt by
 * the initiator still gets the more actionable of the two answers.
 */
export async function POST(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const body: unknown = await req.json().catch(() => ({}));
  return bffProxy(`/governance-approvals/${encodeURIComponent(id)}/decide`, {
    method: "POST",
    body,
  });
}
