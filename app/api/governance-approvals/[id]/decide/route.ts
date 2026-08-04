import { type NextRequest } from "next/server";

import { getSession } from "@/lib/auth/session";
import { sessionIdentityId } from "@/lib/auth/access-scope";
import { effectivePlatformRole } from "@/lib/auth/effective-role";
import { canDecideRequest } from "@/lib/requests/routing";
import type { PlatformRole } from "@/lib/roles";
import {
  decideGovernanceApproval,
  getGovernanceApproval,
} from "@/lib/mock/governance-approval-fixtures";
import { activateProject, rejectProjectCreation, setProjectArchived } from "@/lib/mock/project-fixtures";
import { activateModelProvider, rejectModelProvider } from "@/lib/mock/model-fixtures";
import { patchWorkspace } from "@/lib/mock/workspace-fixtures";
import { publishVersion } from "@/lib/mock/agent-profile-fixtures";
import type { GovernanceApprovalDecisionInput } from "@/lib/schemas/governance-approval";

// DUMMY-DATA SEAM: mutates the shared fixture stores directly. Mirrored in
// mocks/handlers.ts — see [[msw-dual-runtime-mutation-rule]].
export async function POST(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });

  const { id } = await params;
  const body = (await req.json()) as GovernanceApprovalDecisionInput;
  const approval = getGovernanceApproval(id);
  if (!approval) return Response.json({ code: "not_found" }, { status: 404 });

  // ENFORCED HERE, not only in the sheet. `canDecideRequest` renders the
  // buttons; without this the endpoint accepted a decision from anyone signed
  // in — an Organization Admin could close a request still sitting with a
  // Business Unit Admin, skipping the ladder that the escalate route exists to
  // walk. Self-approval is blocked for the same reason it is in routing:
  // §33.2 makes it a rule, not a nicety.
  const eligibility = canDecideRequest({
    currentApproverRole: (approval.currentApproverRole as PlatformRole | null) ?? null,
    requestedById: approval.requestedById,
    status: approval.status,
    viewerRole: effectivePlatformRole(session),
    viewerIdentityId: sessionIdentityId(session),
  });
  if (!eligibility.allowed) {
    return Response.json(
      { code: "forbidden", message: eligibility.reason ?? "Not yours to decide." },
      { status: 403 },
    );
  }

  const decided = decideGovernanceApproval(id, body.decision, session.user.name, body.reason);
  if (!decided) return Response.json({ code: "not_found" }, { status: 404 });

  if (approval.type === "project_creation") {
    if (body.decision === "approve") activateProject(approval.targetRef, session.user.name);
    else rejectProjectCreation(approval.targetRef, session.user.name, body.reason);
  } else if (approval.type === "model_credential") {
    if (body.decision === "approve") activateModelProvider(approval.targetRef, session.user.name);
    else rejectModelProvider(approval.targetRef, session.user.name, body.reason);
  } else if (approval.type === "budget_increase" && body.decision === "approve") {
    const requestedAmountUsd = approval.payload?.requestedAmountUsd;
    if (typeof requestedAmountUsd === "number") {
      patchWorkspace(approval.targetRef, { monthlyBudgetUsd: requestedAmountUsd });
    }
  } else if (approval.type === "project_archive" && body.decision === "approve") {
    setProjectArchived(approval.targetRef, true);
  } else if (
    (approval.type === "agent_default_org" ||
      approval.type === "agent_default_workspace" ||
      approval.type === "agent_default_project") &&
    body.decision === "approve"
  ) {
    // targetRef is the draft version's id, created up-front by the proposer
    // (see app/api/agent-profiles/[id]/propose/route.ts) — approving just
    // publishes it, same as an owner's direct publish. published_by is the
    // approver, not the drafter — see AgentProfileVersion.published_by.
    publishVersion(approval.targetRef, session.user.name);
  }

  return Response.json(decided);
}
