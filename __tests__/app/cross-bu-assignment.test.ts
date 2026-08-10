import { describe, expect, it } from "vitest";

import { applyCrossBuAssignment, requestCrossBuAssignment } from "@/lib/mock/cross-bu";
import { hasCrossBuGrant, revokeCrossBuGrant } from "@/lib/mock/cross-bu-fixtures";
import {
  listProjectMembers,
  parentBusinessUnitOf,
  projectMembershipBlock,
  seatProjectMember,
} from "@/lib/mock/project-membership-fixtures";
import { listMembershipsForIdentity } from "@/lib/mock/workspace-fixtures";
import {
  resolveAccessScope,
  canReadBusinessUnit,
  canReadGovernanceApproval,
  canReadProject,
} from "@/lib/mock/access-scope";
import { listGovernanceApprovals } from "@/lib/mock/governance-approval-fixtures";
import { scopeUserDirectory } from "@/lib/mock/user-directory-fixtures";
import { listNotifications } from "@/lib/mock/notification-fixtures";
import { initialApproverRole } from "@/lib/requests/routing";
import { CROSS_BU_ASSIGNABLE_ROLES } from "@/lib/roles";
import type { GovernanceApproval } from "@/lib/schemas/governance-approval";

/**
 * Borrowing a contributor from another Business Unit.
 *
 * Diego is a Developer in PAYMENTS. `core-ledger` is a LENDING project, run by
 * Ana and governed by Farah — Lending's admin. Lending wants Diego; Marcus,
 * who runs Payments, is the only person who can say yes.
 */
const DIEGO = "idn_diego";
const LENDING_PROJECT = "core-ledger";
const PAYMENTS = "ws_payments";
const LENDING = "ws_lending";

function raise(role = "developer") {
  return requestCrossBuAssignment({
    projectId: LENDING_PROJECT,
    email: "diego@abcbank.com",
    roleName: role,
    reason: "Needs a second developer on the ledger cutover.",
    actorName: "Ana Silva",
    actorIdentityId: "idn_ana",
    actorRole: "project_admin",
  });
}

/**
 * The seeded loan, and whose inbox it lands in.
 *
 * `gov_xbu_omar` exists so the decision card is not dead UI on first load. It
 * is only worth seeding if the routing actually delivers it, and the delivery
 * runs through two independent filters — `listGovernanceApprovals(workspaceId)`
 * and `canReadGovernanceApproval` — which is the pair asserted here, because
 * the route handler and the MSW handler both compose exactly those two.
 */
describe("the seeded cross-unit loan waiting in the inbox", () => {
  const seeded = () => listGovernanceApprovals().find((a) => a.id === "gov_xbu_omar")!;

  it("is pending, and addressed to a Business Unit Admin", () => {
    const a = seeded();
    expect(a, "the seeded loan is missing — the inbox demo is dead UI again").toBeDefined();
    expect(a.type).toBe("cross_bu_assignment");
    expect(a.status).toBe("pending_review");
    expect(a.currentApproverRole).toBe("bu_admin");
    // The PARENT unit owns the decision, so the row is filed under it — not
    // under Lending, which is the side doing the asking.
    expect(a.workspaceId).toBe("ws_platform");
    expect(a.projectId).toBe("core-ledger");
  });

  it("reaches Platform Engineering's admin and no other unit's", () => {
    const a = seeded();
    const scopeFor = (identityId: string) => resolveAccessScope(identityId, "bu_admin");

    // Noah runs Platform Engineering — Omar's unit, so Omar is his to lend.
    expect(canReadGovernanceApproval(scopeFor("idn_noah"), a.workspaceId, a.projectId)).toBe(true);
    // Farah runs Lending, the borrowing side. Wanting him is not standing to
    // approve him.
    expect(canReadGovernanceApproval(scopeFor("idn_farah"), a.workspaceId, a.projectId)).toBe(
      false,
    );
    // Payments has no part in this at all.
    expect(canReadGovernanceApproval(scopeFor("idn_marcus"), a.workspaceId, a.projectId)).toBe(
      false,
    );

    // And the "mine" toggle, which narrows by the unit named on the row.
    expect(listGovernanceApprovals("ws_platform").some((x) => x.id === a.id)).toBe(true);
    expect(listGovernanceApprovals("ws_lending").some((x) => x.id === a.id)).toBe(false);
  });

  it("is not filtered out as the approver's own request", () => {
    // The queue drops rows you raised yourself, because self-approval is
    // refused at the endpoint and offering the decision anyway is a button that
    // can only fail. That filter must not eat the seed.
    expect(seeded().requestedById).not.toBe("idn_noah");
  });

  it("carries everything approving it needs, so the seed applies like a live ask", () => {
    // A seeded row with a thin payload approves into nothing: the grant is
    // written from these fields, and the seat is written from the grant.
    expect(seeded().payload).toMatchObject({
      identityId: "idn_omar",
      roleName: "developer",
      parentWorkspaceId: "ws_platform",
      targetWorkspaceId: "ws_lending",
    });
    expect(CROSS_BU_ASSIGNABLE_ROLES).toContain("developer");
  });

  it("names someone who is genuinely borrowable", () => {
    // Omar belongs to Platform Engineering and holds no seat on core-ledger, so
    // the ask is real rather than a request for something already true.
    expect(parentBusinessUnitOf("idn_omar")).toBe("ws_platform");
    expect(listProjectMembers("core-ledger").some((m) => m.identity.id === "idn_omar")).toBe(false);
    expect(projectMembershipBlock("core-ledger", "omar@abcbank.com")).toContain(
      "Platform Engineering",
    );
  });
});

describe("asking for someone from another business unit", () => {
  it("refuses to seat them without an approval", () => {
    const blocked = projectMembershipBlock(LENDING_PROJECT, "diego@abcbank.com");
    expect(blocked).toContain("Payments");
    // And it says what to do instead, rather than only refusing.
    expect(blocked).toContain("admin");
  });

  it("routes the request to the CONTRIBUTOR'S own unit, not the asker's", () => {
    const { status, body } = raise();
    expect(status).toBe(201);

    const approval = body as GovernanceApproval;
    expect(approval.type).toBe("cross_bu_assignment");
    // Payments — Diego's parent unit. Lending raised it; Lending cannot grant
    // it, so a request landing in Lending's own queue would be self-approval.
    expect(approval.workspaceId).toBe(PAYMENTS);
    expect(approval.currentApproverRole).toBe("bu_admin");
    expect(approval.projectId).toBe(LENDING_PROJECT);
    expect(approval.payload).toMatchObject({
      identityId: DIEGO,
      roleName: "developer",
      parentWorkspaceId: PAYMENTS,
      targetWorkspaceId: LENDING,
    });
  });

  it("tells Payments' admin, and not every other unit's", () => {
    expect(listNotifications("idn_marcus", "bu_admin").some((n) => n.title.includes("Diego"))).toBe(
      true,
    );
    expect(listNotifications("idn_noah", "bu_admin").some((n) => n.title.includes("Diego"))).toBe(
      false,
    );
  });

  it("stays with the peer admin when a BU Admin raises it, instead of climbing", () => {
    // The general rule bumps a request one tier when the requester holds the
    // approving role, so nobody decides their own tier's ask. Here the approver
    // is a DIFFERENT unit's admin, so bumping sent it to the Organization
    // Admin — who has no standing to lend another unit's people.
    expect(initialApproverRole("cross_bu_assignment", "bu_admin")).toBe("bu_admin");
    expect(initialApproverRole("cross_bu_assignment", "project_admin")).toBe("bu_admin");
    // …and the general rule is untouched for everything else.
    expect(initialApproverRole("project_creation", "bu_admin")).toBe("org_admin");
  });

  it("refuses a second request while one is in flight", () => {
    expect(raise().status).toBe(409);
  });

  it("refuses the two admin tiers outright", () => {
    for (const role of ["org_admin", "bu_admin"]) {
      const { status } = requestCrossBuAssignment({
        projectId: LENDING_PROJECT,
        email: "wei@abcbank.com",
        roleName: role,
        actorName: "Ana Silva",
        actorIdentityId: "idn_ana",
        actorRole: "project_admin",
      });
      expect(status, `${role} was accepted`).toBe(422);
    }
    expect(CROSS_BU_ASSIGNABLE_ROLES).not.toContain("bu_admin");
    expect(CROSS_BU_ASSIGNABLE_ROLES).not.toContain("org_admin");
    expect(CROSS_BU_ASSIGNABLE_ROLES).toContain("project_admin");
  });

  it("refuses someone already in the project's own unit", () => {
    // Sofia is in Lending; core-ledger is a Lending project. Nothing to borrow.
    const { status } = requestCrossBuAssignment({
      projectId: LENDING_PROJECT,
      email: "sofia@abcbank.com",
      roleName: "ba",
      actorName: "Ana Silva",
      actorIdentityId: "idn_ana",
      actorRole: "project_admin",
    });
    expect(status).toBe(422);
  });

  it("refuses an email nobody uses, rather than minting a person", () => {
    const { status } = requestCrossBuAssignment({
      projectId: LENDING_PROJECT,
      email: "ghost@nowhere.test",
      roleName: "developer",
      actorName: "Ana Silva",
      actorIdentityId: "idn_ana",
      actorRole: "project_admin",
    });
    expect(status).toBe(404);
  });
});

describe("once the parent admin approves", () => {
  it("seats them, and leaves their business unit alone", () => {
    // A second project, in a THIRD unit — Platform Engineering — so the
    // Payments→Lending request raised above stays open and untouched.
    const open = requestCrossBuAssignment({
      projectId: "recon-bots",
      email: "diego@abcbank.com",
      roleName: "developer",
      actorName: "Ravi Sharma",
      actorIdentityId: "idn_ravi",
      actorRole: "project_admin",
    });
    const created = open.body as GovernanceApproval;
    expect(open.status).toBe(201);

    applyCrossBuAssignment(created, "Marcus Reyes");

    // On the project…
    expect(listProjectMembers("recon-bots").some((m) => m.identity.id === DIEGO)).toBe(true);
    // …marked as a guest, so the borrowing admin knows whose person this is.
    const seat = listProjectMembers("recon-bots").find((m) => m.identity.id === DIEGO)!;
    expect(seat.homeBusinessUnitName).toBe("Payments");
    expect(seat.role).toBe("developer");

    // Their Business Unit is untouched — ownership never moved.
    expect(parentBusinessUnitOf(DIEGO)).toBe(PAYMENTS);
    expect(listMembershipsForIdentity(DIEGO).map((m) => String(m.workspaceId))).toEqual([PAYMENTS]);
  });

  it("gives them the project and NOTHING else in that unit", () => {
    // The containment the whole feature turns on: one project, not the unit.
    const scope = resolveAccessScope(DIEGO, "developer");
    expect(canReadProject(scope, "recon-bots")).toBe(true);
    expect(canReadBusinessUnit(scope, "ws_platform")).toBe(false);
    // Nor the borrowing unit's other work.
    expect(canReadProject(scope, "mobile-onboarding")).toBe(false);
    // Their own unit is unchanged.
    expect(canReadBusinessUnit(scope, PAYMENTS)).toBe(true);
  });

  it("tells the person they were lent", () => {
    expect(
      listNotifications(DIEGO, "developer").some((n) => n.title.includes("Reconciliation")),
    ).toBe(true);
  });

  it("puts the guest in the BORROWING admin's user list, marked as one", () => {
    // The gap this closes: a borrowed contributor works on your project and
    // against your budget, but belongs to another unit — so they appeared in
    // nobody's directory that you could read. Listed now, under the unit that
    // actually owns them, and not editable by you.
    const platformAdmin = {
      isOrgWide: false,
      businessUnitIds: ["ws_platform"],
      managedBusinessUnitIds: ["ws_platform"],
      actingBindings: [{ kind: "business_unit", scopeId: "ws_platform" }],
    };
    const diego = scopeUserDirectory(platformAdmin).find((e) => e.identityId === DIEGO);
    expect(diego, "the borrowed contributor is invisible to the unit using them").toBeDefined();
    expect(diego!.isGuest).toBe(true);
    // Named under Payments — claiming they are in Platform Engineering would be
    // the lie the marker exists to avoid.
    expect(diego!.businessUnitName).toBe("Payments");

    // Their own unit's admin still sees them as an ordinary member.
    const paymentsAdmin = {
      isOrgWide: false,
      businessUnitIds: [PAYMENTS],
      managedBusinessUnitIds: [PAYMENTS],
      actingBindings: [{ kind: "business_unit", scopeId: PAYMENTS }],
    };
    expect(scopeUserDirectory(paymentsAdmin).find((e) => e.identityId === DIEGO)!.isGuest).toBe(
      false,
    );

    // A unit with no loan into it sees him too — every unit admin reads the
    // whole organisation — but NOT as their guest. The marker is what is
    // per-unit, not the visibility: it says "working here, on loan", and
    // Lending has borrowed nobody.
    const lendingAdmin = {
      isOrgWide: false,
      businessUnitIds: [LENDING],
      managedBusinessUnitIds: [LENDING],
      actingBindings: [{ kind: "business_unit", scopeId: LENDING }],
    };
    const seenByLending = scopeUserDirectory(lendingAdmin).find((e) => e.identityId === DIEGO)!;
    expect(seenByLending).toBeDefined();
    expect(seenByLending.isGuest, "Lending borrowed nobody, yet claims a guest").toBe(false);
    expect(seenByLending.businessUnitName).toBe("Payments");
  });

  it("lets the seat exist only because the grant does", () => {
    expect(hasCrossBuGrant(DIEGO, "recon-bots")).toBe(true);
    expect(projectMembershipBlock("recon-bots", "diego@abcbank.com")).toBeNull();

    // Take the grant away and the same seat becomes unauthorised again — the
    // block is reading the approval, not a one-time exception.
    revokeCrossBuGrant(DIEGO, "recon-bots");
    expect(projectMembershipBlock("recon-bots", "diego@abcbank.com")).toContain("Payments");
    expect(seatProjectMember("recon-bots", DIEGO, "developer")).toHaveProperty("error");
  });
});
