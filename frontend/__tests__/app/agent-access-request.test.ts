import { beforeEach, describe, expect, it } from "vitest";

import {
  createGovernanceApproval,
  decideGovernanceApproval,
} from "@/lib/mock/governance-approval-fixtures";
import {
  agentAccessApprover,
  agentOwnerRole,
  canRaiseType,
  nextAgentAccessStage,
} from "@/lib/requests/routing";

function raise(phase = "requirements") {
  return createGovernanceApproval({
    type: "agent_access",
    workspaceId: "ws_payments",
    workspaceName: "Payments",
    projectId: "payments-api",
    projectName: "Payments API",
    title: `Access to the ${phase} agent`,
    summary: "Needs it to triage the defect",
    requestedBy: "Grace Hopper",
    requestedById: "u_member",
    requestedByRole: "developer",
    targetRef: `payments-api::${phase}`,
    payload: { phase },
    description: "I am covering the BA this sprint and cannot open the requirements agent.",
  });
}

describe("who decides an agent-access request", () => {
  it("is raisable by a contributor", () => {
    expect(canRaiseType("developer", "agent_access")).toBe(true);
  });

  it("is not raisable by the Organization Admin, who has no agent access at all", () => {
    expect(canRaiseType("org_admin", "agent_access")).toBe(false);
  });

  it("uses the same owner map the agent gates use", () => {
    expect(agentOwnerRole("requirements")).toBe("ba");
    expect(agentOwnerRole("design")).toBe("architect");
    // Development is owned — built AND approved — by the Developer since the
    // ownership table became one-agent-one-role. It reads the canonical map rather
    // than re-deriving one, which is what makes a move like that show up here.
    expect(agentOwnerRole("development")).toBe("developer");
    expect(agentOwnerRole("deployment")).toBe("devops_engineer");
  });

  it("starts with the Project Admin whoever raised it", () => {
    expect(agentAccessApprover("project_admin", "requirements")).toBe("project_admin");
  });

  it("ends after the owner", () => {
    expect(nextAgentAccessStage("project_admin", "requirements")).toBe("agent_owner");
    expect(nextAgentAccessStage("agent_owner", "requirements")).toBeNull();
  });

  it("gives every agent a delivery owner to route stage two to", () => {
    // Documentation used to be the exception: the Project Admin owned it, so stage
    // one already WAS the owner's decision and advancing would have handed the
    // request back to whoever just decided it. It belongs to the BA now, so no
    // phase short-circuits any more and every request has both stages.
    expect(agentOwnerRole("documentation")).toBe("ba");
    expect(nextAgentAccessStage("project_admin", "documentation")).toBe("agent_owner");
  });

  it("still refuses to route a stage back to the role that just decided it", () => {
    // The guard above is now unreachable through AGENT_OWNER_ROLE, since every
    // phase has a delivery owner. Keep testing it directly: it is the reason the
    // chain cannot loop, and a future owner move could make it live again.
    expect(nextAgentAccessStage("agent_owner", "documentation")).toBeNull();
  });
});

describe("the two-stage chain", () => {
  let id: string;

  beforeEach(() => {
    id = raise().id;
  });

  it("opens with the Project Admin and a stage recorded", () => {
    const created = raise();
    expect(created.currentApproverRole).toBe("project_admin");
    expect(created.approvalStage).toBe("project_admin");
    expect(created.status).toBe("pending_review");
  });

  it("ADVANCES rather than closing when the Project Admin approves", () => {
    const after = decideGovernanceApproval(id, "approve", "Ada Lovelace", "Covering the BA");

    // The half that matters: an approval here must not read as access granted.
    expect(after?.status).toBe("pending_review");
    expect(after?.approvalStage).toBe("agent_owner");
    expect(after?.currentApproverRole).toBe("ba");
    expect(after?.decidedAt).toBeNull();
  });

  it("records the hand-off as an assignment, not an escalation", () => {
    const after = decideGovernanceApproval(id, "approve", "Ada Lovelace");
    const kinds = after!.timeline.map((e) => e.kind);

    expect(kinds).toContain("approved");
    expect(kinds).toContain("assigned");
    // An escalation would mean the first approver never answered. They did.
    expect(kinds).not.toContain("escalated");
    expect(after!.escalationCount).toBe(0);
  });

  it("closes only when the agent owner approves", () => {
    decideGovernanceApproval(id, "approve", "Ada Lovelace");
    const final = decideGovernanceApproval(id, "approve", "Brian Kernighan");

    expect(final?.status).toBe("approved");
    expect(final?.approvalStage).toBeNull();
    expect(final?.currentApproverRole).toBeNull();
    expect(final?.decidedAt).not.toBeNull();
  });

  it("closes immediately on a rejection at stage one", () => {
    // One no is enough — the owner should not be asked to overturn it.
    const after = decideGovernanceApproval(id, "reject", "Ada Lovelace", "Not this sprint");

    expect(after?.status).toBe("rejected");
    expect(after?.approvalStage).toBeNull();
    expect(after?.currentApproverRole).toBeNull();
  });

  it("closes on a rejection at stage two", () => {
    decideGovernanceApproval(id, "approve", "Ada Lovelace");
    const after = decideGovernanceApproval(id, "reject", "Brian Kernighan");

    expect(after?.status).toBe("rejected");
    expect(after?.approvalStage).toBeNull();
  });

  it("routes stage two by the phase asked for", () => {
    const designId = raise("design").id;
    const after = decideGovernanceApproval(designId, "approve", "Ada Lovelace");

    expect(after?.currentApproverRole).toBe("architect");
  });

  it("routes a Documentation request on to the BA, its new owner", () => {
    // This used to close at stage one, back when the Project Admin owned
    // Documentation. It is the BA's agent now, so the request advances like any
    // other rather than being approved by the same person twice.
    const docsId = raise("documentation").id;
    const after = decideGovernanceApproval(docsId, "approve", "Ada Lovelace");

    expect(after?.status).toBe("pending_review");
    expect(after?.currentApproverRole).toBe("ba");
  });
});

describe("single-stage types are unaffected", () => {
  it("closes a connector request on the first approval", () => {
    const created = createGovernanceApproval({
      type: "connector_access",
      workspaceId: "ws_payments",
      workspaceName: "Payments",
      title: "Connect Slack",
      summary: "For release notes",
      requestedBy: "Grace Hopper",
      requestedByRole: "developer",
      targetRef: "slack",
    });
    expect(created.approvalStage).toBeNull();

    const after = decideGovernanceApproval(created.id, "approve", "Ada Lovelace");
    expect(after?.status).toBe("approved");
    expect(after?.currentApproverRole).toBeNull();
  });
});
