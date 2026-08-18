import { describe, expect, it } from "vitest";

import {
  createGovernanceApproval,
  escalateGovernanceApproval,
} from "@/lib/mock/governance-approval-fixtures";
import { emitNotification, listNotifications } from "@/lib/mock/notification-fixtures";
import { notificationViewer } from "@/lib/mock/access-scope";

/**
 * WHO A ROLE-ADDRESSED NOTIFICATION REACHES.
 *
 * The interesting assertions are the negative ones. A bell that shows too much is
 * not a cosmetic problem — a request title says which unit is over budget, so an
 * unaddressed listing is a scope leak wearing a dropdown.
 *
 * `role: "bu_admin"` used to mean every Business Unit Admin in the organisation,
 * which is how Lending's admin came to read Payments' business. Marcus runs
 * Payments, Farah runs Lending, Noah runs Platform Engineering — three admins
 * holding the same role name in three different places, which is the only
 * arrangement in which this bug is visible at all.
 */
const MARCUS = "idn_marcus";
const FARAH = "idn_farah";
const PAYMENTS = "ws_payments";
const LENDING = "ws_lending";

function titles(identityId: string, role: Parameters<typeof notificationViewer>[1]) {
  return listNotifications(notificationViewer(identityId, role)).map((n) => n.title);
}

describe("a role address names a scope", () => {
  it("reaches the admin of the unit it names, and no other unit's", () => {
    emitNotification({
      kind: "budget_near_cap",
      title: "Payments is near its cap",
      role: "bu_admin",
      scopeKind: "business_unit",
      scopeId: PAYMENTS,
    });

    expect(titles(MARCUS, "bu_admin")).toContain("Payments is near its cap");
    expect(titles(FARAH, "bu_admin")).not.toContain("Payments is near its cap");
  });

  it("refuses a scope-less role address rather than delivering it everywhere", () => {
    // Undeliverable beats broadly deliverable: dropping one notification is a
    // smaller failure than putting one unit's business in every other unit's bell.
    const created = emitNotification({
      kind: "budget_near_cap",
      title: "Addressed to every unit at once",
      role: "bu_admin",
    });

    expect(created).toBeNull();
    expect(titles(MARCUS, "bu_admin")).not.toContain("Addressed to every unit at once");
    expect(titles(FARAH, "bu_admin")).not.toContain("Addressed to every unit at once");
  });

  it("still delivers the personal half when only the role half is unaddressable", () => {
    const created = emitNotification({
      kind: "request_approved",
      title: "Yours was approved",
      identityId: MARCUS,
      role: "bu_admin",
    });

    expect(created).not.toBeNull();
    expect(created!.role).toBeNull();
    expect(titles(MARCUS, "bu_admin")).toContain("Yours was approved");
    expect(titles(FARAH, "bu_admin")).not.toContain("Yours was approved");
  });

  it("needs no scope for the organization's own queue", () => {
    // There is one Organization Admin queue, so it addresses without a scope and
    // is matched against the role the viewer is ACTING as — org-wide standing does
    // not come from a membership row, so a queue match would miss it.
    const created = emitNotification({
      kind: "request_escalated",
      title: "Escalated to the Organization Admin",
      role: "org_admin",
    });

    expect(created).not.toBeNull();
    expect(titles("idn_nobody", "org_admin")).toContain("Escalated to the Organization Admin");
    expect(titles(FARAH, "bu_admin")).not.toContain("Escalated to the Organization Admin");
  });
});

describe("a request's queue is the approver's, in the requesting unit", () => {
  it("does not put one unit's approval in another unit's bell", () => {
    createGovernanceApproval({
      type: "budget_increase",
      workspaceId: LENDING,
      workspaceName: "Lending",
      title: "Lending needs a higher cap",
      summary: "The ledger cutover overran.",
      requestedBy: "Ana Silva",
      requestedById: "idn_ana",
      requestedByRole: "project_admin",
      targetRef: LENDING,
    });

    // It routed somewhere; wherever that is, it is not Payments' problem.
    expect(titles(MARCUS, "bu_admin")).not.toContain("Lending needs a higher cap");
  });

  it("keeps an escalation inside the unit it came from", () => {
    const created = createGovernanceApproval({
      type: "access_request",
      workspaceId: LENDING,
      workspaceName: "Lending",
      title: "Lending wants the deploy pipeline",
      summary: "Cannot ship without it.",
      requestedBy: "Ana Silva",
      requestedById: "idn_ana",
      requestedByRole: "developer",
      targetRef: LENDING,
    });
    escalateGovernanceApproval(created.id, "Ana Silva", "No answer for a week");

    expect(titles(MARCUS, "bu_admin")).not.toContain("Lending wants the deploy pipeline");
  });
});
