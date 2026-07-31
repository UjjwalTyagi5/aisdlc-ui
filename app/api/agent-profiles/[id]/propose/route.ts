import { type NextRequest } from "next/server";

import { getSession } from "@/lib/auth/session";
import { agentDefaultApprovalType } from "@/lib/governance";
import { createGovernanceApproval } from "@/lib/mock/governance-approval-fixtures";

interface ProposeInput {
  scope: "org" | "workspace" | "project";
  agentId: string;
  agentLabel: string;
  workspaceId?: string;
  workspaceName?: string;
  projectId?: string;
  projectName?: string;
}

// DUMMY-DATA SEAM: creates a GovernanceApproval routed to that tier's owner
// (AGENT_DEFAULT_OWNER_ROLE) instead of publishing directly — used when the
// viewer proposes a change to an Agent Studio default they don't own. `id`
// is the draft version's id (already saved via /api/agent-profiles/draft);
// approving just publishes it (see governance-approvals/[id]/decide).
// Mirrored in mocks/handlers.ts — see [[msw-dual-runtime-mutation-rule]].
export async function POST(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });

  const { id } = await params;
  const body = (await req.json()) as ProposeInput;
  const scopeLabel =
    body.scope === "org" ? "organization" : body.scope === "workspace" ? "business unit" : "project";

  const created = createGovernanceApproval({
    type: agentDefaultApprovalType(body.scope),
    workspaceId: body.workspaceId ?? "",
    workspaceName: body.workspaceName ?? "",
    projectId: body.projectId ?? null,
    projectName: body.projectName ?? null,
    title: `${body.agentLabel} default change (${scopeLabel})`,
    summary: `${session.user.name} proposed a ${body.agentLabel} behavior change for the ${scopeLabel} default.`,
    requestedBy: session.user.name,
    targetRef: id,
    payload: { agentId: body.agentId, scope: body.scope },
  });
  return Response.json(created, { status: 201 });
}
