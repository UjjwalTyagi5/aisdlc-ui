import { type NextRequest } from "next/server";

import { getWorkspace } from "@/lib/mock/workspace-fixtures";
import { createGovernanceApproval } from "@/lib/mock/governance-approval-fixtures";
import { getSession } from "@/lib/auth/session";

// DUMMY-DATA SEAM: a BU Admin's request for more budget than they can set
// themselves — routed to Org Admin (lib/governance.ts::GOVERNANCE_APPROVER_ROLE).
// Mirrored in mocks/handlers.ts — see [[msw-dual-runtime-mutation-rule]].
export async function POST(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });

  const { id } = await params;
  const workspace = getWorkspace(id);
  if (!workspace) return Response.json({ code: "not_found", message: "not found" }, { status: 404 });

  const body = (await req.json()) as { requestedAmountUsd?: number; reason?: string };
  if (!body?.requestedAmountUsd || body.requestedAmountUsd <= 0) {
    return Response.json(
      { code: "invalid_input", message: "requestedAmountUsd must be a positive number" },
      { status: 422 },
    );
  }

  const created = createGovernanceApproval({
    type: "budget_increase",
    workspaceId: workspace.id,
    workspaceName: workspace.displayName,
    title: `Budget increase: ${workspace.displayName}`,
    summary: `${session.user.name} requested a monthly budget increase to $${body.requestedAmountUsd.toLocaleString()} for ${workspace.displayName}${body.reason ? ` — "${body.reason}"` : ""}.`,
    requestedBy: session.user.name,
    targetRef: workspace.id,
    payload: { requestedAmountUsd: body.requestedAmountUsd },
  });
  return Response.json(created, { status: 201 });
}
