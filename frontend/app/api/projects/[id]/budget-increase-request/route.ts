import { type NextRequest } from "next/server";

import { bffProxy } from "@/lib/bff/proxy";

/**
 * A Project Admin asking for more budget than they may set themselves —
 * proxied to FastAPI `POST /projects/{id}/budget-increase-request`.
 *
 * Mirrors `app/api/workspaces/[id]/budget-increase-request/route.ts`: a
 * Project Admin's own cap edit applies directly, but raising it past what
 * they may set becomes a `budget_increase` request routed to the Business
 * Unit Admin above them instead of the Org Admin (workspaces escalate one
 * tier higher than projects do).
 *
 * A dedicated endpoint rather than a plain `POST /governance-approvals` because
 * the AMOUNT has to be recorded server-side: the generic create accepts no
 * payload, so a client cannot set the figure that approving will apply.
 * Approving reads it back from the stored request, so the number agreed to
 * is the number applied.
 */
export async function POST(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const body = (await req.json().catch(() => ({}))) as { requestedAmountUsd?: number };
  if (!body?.requestedAmountUsd || body.requestedAmountUsd <= 0) {
    return Response.json(
      { code: "invalid_input", message: "requestedAmountUsd must be a positive number" },
      { status: 422 },
    );
  }
  return bffProxy(`/projects/${encodeURIComponent(id)}/budget-increase-request`, {
    method: "POST",
    body,
  });
}
