import { type NextRequest } from "next/server";

import { bffProxy } from "@/lib/bff/proxy";

/**
 * A Business Unit Admin asking for more budget than they may set themselves —
 * proxied to FastAPI `POST /workspaces/{id}/budget-increase-request`.
 *
 * The other half of the budget cascade: a unit's own Admin sets the FIRST cap
 * directly (someone has to fill in a blank the Org Admin left), and changing one
 * that already exists comes here, as a `budget_increase` request routed to the Org
 * Admin.
 *
 * A dedicated endpoint rather than a plain `POST /governance-approvals` because the
 * AMOUNT has to be recorded server-side: the generic create accepts no payload, so
 * a client cannot set the figure that approving will apply. Approving reads it back
 * from the stored request, so the number agreed to is the number applied.
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
  return bffProxy(`/workspaces/${encodeURIComponent(id)}/budget-increase-request`, {
    method: "POST",
    body,
  });
}
