import { describe, expect, it } from "vitest";

import {
  assignBusinessUnitRole,
  changeOrgAppointment,
  onboardIntoOrganization,
} from "@/lib/mock/onboarding";
import { listUserDirectory, scopeUserDirectory } from "@/lib/mock/user-directory-fixtures";
import { visibleNav } from "@/lib/nav";
import { ROLE_PERMISSIONS } from "@/lib/auth/role-permissions";
import { listNotifications } from "@/lib/mock/notification-fixtures";
import { notificationViewer } from "@/lib/mock/access-scope";
import { getOrgRole } from "@/lib/mock/org-role-fixtures";
import {
  findOpenRoleAssignment,
  listGovernanceApprovals,
} from "@/lib/mock/governance-approval-fixtures";
import {
  listAllMemberships,
  setMembershipRole,
  listMembershipsForIdentity,
} from "@/lib/mock/workspace-fixtures";
import {
  addProjectMember,
  listProjectMembershipsForIdentity,
  projectMembershipBlock,
} from "@/lib/mock/project-membership-fixtures";
import { canWriteCustomRole, resolveRoleOwner } from "@/lib/mock/custom-role-fixtures";
import { ORG_ASSIGNABLE_ROLES, ROLE_META } from "@/lib/roles";
import { BUSINESS_UNIT_ASSIGNABLE_BUILTIN_ROLES } from "@/hooks/use-assignable-roles";
import type { CustomRole } from "@/lib/api/roles";

/**
 * The two-person onboarding handover: what the Organization Admin may decide,
 * what is left to the Business Unit Admin, and the message that connects them.
 *
 * These assert the RULES rather than the UI, because the rules are what the
 * pickers are generated from — a dropdown that stops offering Developer while
 * the endpoint still accepts it has fixed nothing.
 */

const PLATFORM_ENG = "ws_platform"; // Noah Bennett (idn_noah) is its bu_admin.

describe("what an Organization Admin may assign", () => {
  it("offers exactly two roles, and neither is a delivery role", () => {
    expect([...ORG_ASSIGNABLE_ROLES]).toEqual(["bu_admin", "contributor"]);
  });

  it("refuses every Business Unit role at the endpoint, not just in the picker", () => {
    for (const role of ["developer", "ba", "architect", "qa", "devops_engineer", "scrum_master"]) {
      const { status, body } = onboardIntoOrganization({
        email: `${role}@example.test`,
        workspaceId: PLATFORM_ENG,
        role,
      });
      expect(status, `${role} was accepted`).toBe(422);
      expect((body as { code: string }).code).toBe("invalid_role");
    }
  });

  it("refuses a Contributor with no business unit — they would belong to nobody", () => {
    const { status } = onboardIntoOrganization({
      email: "nowhere@example.test",
      role: "contributor",
    });
    expect(status).toBe(422);
  });

  it("appoints a Business Unit Admin with no unit yet, and records no membership", () => {
    const { status, body } = onboardIntoOrganization({
      email: "unplaced-admin@example.test",
      displayName: "Unplaced Admin",
      role: "bu_admin",
    });
    expect(status).toBe(201);

    const identityId = (body as { identityId: string }).identityId;
    expect(getOrgRole(identityId)).toMatchObject({ role: "bu_admin", businessUnitId: null });
    expect(listMembershipsForIdentity(identityId)).toEqual([]);
  });
});

describe("the handover to the Business Unit Admin", () => {
  it("notifies the unit's admin, and only them", () => {
    const before = listNotifications(notificationViewer("idn_noah", "bu_admin")).length;

    const { status, body } = onboardIntoOrganization({
      email: "newjoiner@example.test",
      displayName: "New Joiner",
      workspaceId: PLATFORM_ENG,
      role: "contributor",
    });
    expect(status).toBe(201);
    expect((body as { notifiedBusinessUnitAdmin: boolean }).notifiedBusinessUnitAdmin).toBe(true);

    const noah = listNotifications(notificationViewer("idn_noah", "bu_admin"));
    expect(noah.length).toBe(before + 1);
    expect(noah[0]).toMatchObject({ kind: "member_awaiting_role" });
    expect(noah[0]!.title).toContain("New Joiner");

    // Farah runs a different unit — the queue is per unit, not a broadcast.
    expect(
      listNotifications(notificationViewer("idn_farah", "bu_admin")).some((n) => n.title.includes("New Joiner")),
    ).toBe(false);
  });

  it("moves the assigned role into the Role column, not the project one", () => {
    // The bug this pins: the directory printed the org-level appointment as the
    // person's role, so someone the unit admin had made a Developer still read
    // "Contributor" while the change showed up only among their project rows.
    const { body } = onboardIntoOrganization({
      email: "promoted@example.test",
      displayName: "Promoted Person",
      workspaceId: PLATFORM_ENG,
      role: "contributor",
    });
    const identityId = (body as { identityId: string }).identityId;

    expect(listUserDirectory().find((e) => e.identityId === identityId)!.unitRole).toBe(
      "contributor",
    );

    assignBusinessUnitRole({
      workspaceId: PLATFORM_ENG,
      userId: identityId,
      roleName: "architect",
    });

    const after = listUserDirectory().find((e) => e.identityId === identityId)!;
    expect(after.unitRole).toBe("architect");
    // The org-level appointment is unchanged — it answered a different
    // question, and the Role column now reads `unitRole` instead.
    expect(after.orgRole).toBe("contributor");
    expect(after.bindings.filter((b) => b.scope === "project")).toEqual([]);
  });

  it("calls a Project Admin a Project Admin", () => {
    const directory = listUserDirectory();
    for (const name of ["Priya Menon", "Ravi Sharma", "Yuki Tanaka"]) {
      expect(directory.find((e) => e.displayName === name)!.unitRole, name).toBe("project_admin");
    }
    // And a governance role has no unit role to print for the Org Admin, whose
    // authority is not a membership row.
    expect(directory.find((e) => e.displayName === "Sarthak Kapoor")!.unitRole).toBeNull();
    expect(directory.find((e) => e.displayName === "Noah Bennett")!.unitRole).toBe("bu_admin");
  });

  it("shows the new person as awaiting a role until one is assigned", () => {
    const { body } = onboardIntoOrganization({
      email: "awaiting@example.test",
      displayName: "Awaiting Person",
      workspaceId: PLATFORM_ENG,
      role: "contributor",
    });
    const identityId = (body as { identityId: string }).identityId;

    const before = listUserDirectory().find((e) => e.identityId === identityId);
    expect(before).toMatchObject({ orgRole: "contributor", awaitingRole: true });
    expect(before!.businessUnitId).toBe(PLATFORM_ENG);

    // What the Business Unit Admin's assign dialog does.
    setMembershipRole(PLATFORM_ENG, identityId, "developer");

    const after = listUserDirectory().find((e) => e.identityId === identityId);
    expect(after!.awaitingRole).toBe(false);
    expect(after!.bindings.map((b) => b.role)).toContain("developer");
  });

  it("seeds at least one person already waiting, so the queue is not dead UI", () => {
    expect(listUserDirectory().some((e) => e.awaitingRole)).toBe(true);
  });

  it("offers the Business Unit Admin real roles, and never the three it may not grant", () => {
    expect(BUSINESS_UNIT_ASSIGNABLE_BUILTIN_ROLES).toContain("developer");
    expect(BUSINESS_UNIT_ASSIGNABLE_BUILTIN_ROLES).toContain("project_admin");
    // The placeholder is the state they were asked to resolve…
    expect(BUSINESS_UNIT_ASSIGNABLE_BUILTIN_ROLES).not.toContain("contributor");
    // …and both admin tiers are appointed from above, never from inside the
    // unit: a unit admin granting `bu_admin` would appoint their own successor.
    expect(BUSINESS_UNIT_ASSIGNABLE_BUILTIN_ROLES).not.toContain("bu_admin");
    expect(BUSINESS_UNIT_ASSIGNABLE_BUILTIN_ROLES).not.toContain("org_admin");
  });
});

describe("the request the handover raises", () => {
  it("lands in Requests & Approvals, addressed to the unit's admin", () => {
    const { body } = onboardIntoOrganization({
      email: "queued@example.test",
      displayName: "Queued Person",
      workspaceId: PLATFORM_ENG,
      role: "contributor",
      actorName: "Sarthak Kapoor",
    });
    const identityId = (body as { identityId: string }).identityId;

    const open = findOpenRoleAssignment(PLATFORM_ENG, identityId);
    expect(open, "no role_assignment request was raised").toBeDefined();
    expect(open!.currentApproverRole).toBe("bu_admin");
    expect(open!.requestedBy).toBe("Sarthak Kapoor");
    expect(open!.title).toContain("Queued Person");
    // The row carries who to assign, so the queue can open the same dialog
    // Users does rather than sending the admin off to find them.
    expect(open!.payload).toMatchObject({ identityId });
  });

  it("closes when the role is assigned, whichever surface did it", () => {
    const { body } = onboardIntoOrganization({
      email: "closed@example.test",
      displayName: "Closed Person",
      workspaceId: PLATFORM_ENG,
      role: "contributor",
    });
    const identityId = (body as { identityId: string }).identityId;
    const requestId = findOpenRoleAssignment(PLATFORM_ENG, identityId)!.id;

    const outcome = assignBusinessUnitRole({
      workspaceId: PLATFORM_ENG,
      userId: identityId,
      roleName: "qa",
      roleLabel: ROLE_META.qa.label,
      actorName: "Noah Bennett",
    });
    expect(outcome.status).toBe(200);
    expect((outcome.body as { resolvedRequestId: string | null }).resolvedRequestId).toBe(
      requestId,
    );

    expect(findOpenRoleAssignment(PLATFORM_ENG, identityId)).toBeUndefined();
    const closed = listGovernanceApprovals().find((a) => a.id === requestId)!;
    expect(closed.status).toBe("approved");
    expect(closed.reason).toContain(ROLE_META.qa.label);
    // And the person waiting hears about it — they were told nothing when they
    // were onboarded, because there was nothing to tell them.
    expect(
      listNotifications(notificationViewer(identityId, null)).some((n) => n.title.includes(ROLE_META.qa.label)),
    ).toBe(true);
  });

  it("refuses to 'assign' the placeholder back", () => {
    const outcome = assignBusinessUnitRole({
      workspaceId: PLATFORM_ENG,
      userId: "idn_tomas",
      roleName: "contributor",
    });
    expect(outcome.status).toBe(422);
  });

  it("does not invent a request to close for an ordinary role change", () => {
    const outcome = assignBusinessUnitRole({
      workspaceId: "ws_payments",
      userId: "idn_wei",
      roleName: "developer",
      roleLabel: ROLE_META.developer.label,
    });
    expect(outcome.status).toBe(200);
    expect((outcome.body as { resolvedRequestId: string | null }).resolvedRequestId).toBeNull();
  });
});

describe("the governance tier is never on a project", () => {
  const governanceIdentities = ["idn_sarthak", "idn_marcus", "idn_farah", "idn_noah"];

  it("holds no project binding in the seeded roster", () => {
    for (const id of governanceIdentities) {
      expect(listProjectMembershipsForIdentity(id), `${id} is on a project`).toEqual([]);
    }
  });

  it("refuses to seat one, by email", () => {
    expect(projectMembershipBlock("payments-api", "farah@abcbank.com")).toContain("never members");
    expect(projectMembershipBlock("payments-api", "srk02804@gmail.com")).toContain("never members");
  });

  it("lets delivery people in their own unit, and unknown emails, through", () => {
    // Diego is in Payments; payments-api is a Payments project.
    expect(projectMembershipBlock("payments-api", "diego@abcbank.com")).toBeNull();
    // Nobody yet — they get enrolled in the project's unit as they are minted.
    expect(projectMembershipBlock("payments-api", "nobody@example.test")).toBeNull();
  });

  it("keeps every person in exactly one business unit", () => {
    // The Organization Admin is the one exception: their row in every unit is
    // the shape their org-wide authority takes, not a membership.
    for (const entry of listUserDirectory()) {
      if (entry.orgRole === "org_admin") continue;
      const units = new Set(
        entry.bindings.filter((b) => b.scope === "business_unit").map((b) => b.id),
      );
      expect([...units], `${entry.displayName} is in more than one business unit`).toHaveLength(
        units.size === 0 ? 0 : 1,
      );
    }
  });

  it("staffs every project from its own business unit", () => {
    // The leak this pins: a Payments person on a Lending project showed up in
    // Lending's people list, and on their own row under a unit they are not a
    // member of.
    for (const entry of listUserDirectory()) {
      if (entry.orgRole === "org_admin") continue;
      const units = new Set(
        entry.bindings.filter((b) => b.scope === "business_unit").map((b) => b.id),
      );
      for (const p of entry.bindings.filter((b) => b.scope === "project")) {
        expect(
          p.businessUnitId !== null && units.has(p.businessUnitId),
          `${entry.displayName} is on ${p.name}, which belongs to a unit they are not in`,
        ).toBe(true);
      }
    }
  });

  it("refuses to seat someone on another unit's project", () => {
    // Diego is in Payments; core-ledger belongs to Lending.
    const blocked = projectMembershipBlock("core-ledger", "diego@abcbank.com");
    expect(blocked).toContain("Payments");
    expect(blocked).toContain("Lending");
  });

  it("enrols a brand-new person into the project's unit as it seats them", () => {
    // Otherwise they are on a project but in no unit: outside every roster,
    // budget and admin's reach, and absent from the people directory.
    addProjectMember("recon-bots", {
      email: "fresh.start@example.test",
      displayName: "Fresh Start",
      roleName: "developer",
    });
    const entry = listUserDirectory().find((e) => e.displayName === "Fresh Start")!;
    expect(entry.businessUnitName).toBe("Platform Engineering");
    expect(entry.unitRole).toBe("contributor");
  });

  it("left no project without an admin when they were removed", () => {
    // Marcus was the project_admin on mobile-onboarding and Farah on
    // recon-bots; taking the governance tier off without seating delivery
    // people in their place would have left two projects unrunnable.
    const adminOf = new Set(
      ["idn_yuki", "idn_ana", "idn_priya", "idn_ravi"].flatMap((id) =>
        listProjectMembershipsForIdentity(id)
          .filter((m) => m.role === "project_admin")
          .map((m) => m.projectId),
      ),
    );
    for (const projectId of ["mobile-onboarding", "core-ledger", "payments-api", "recon-bots"]) {
      expect([...adminOf], `${projectId} has no project admin`).toContain(projectId);
    }
  });

  it("keeps every seeded unit membership to a real role", () => {
    for (const m of listAllMemberships()) {
      expect(m.role in ROLE_META || m.role.startsWith("role_"), `unknown role ${m.role}`).toBe(
        true,
      );
    }
  });
});

describe("who sees which slice of the directory", () => {
  const org = { isOrgWide: true, businessUnitIds: [] };
  // Administering a unit is what separates this scope from the Project Admin's
  // below — the two are otherwise identical, and it is the only difference the
  // directory reads.
  const buAdmin = {
    isOrgWide: false,
    businessUnitIds: [PLATFORM_ENG],
    managedBusinessUnitIds: [PLATFORM_ENG],
    actingBindings: [{ kind: "business_unit", scopeId: PLATFORM_ENG }],
  };
  // A Project Admin on a Platform Engineering project: they can READ the
  // parent unit for context but administer none of it.
  const projectAdmin = {
    isOrgWide: false,
    businessUnitIds: [PLATFORM_ENG],
    actingBindings: [{ kind: "business_unit", scopeId: PLATFORM_ENG }],
  };

  it("gives the whole organisation to the Organization Admin alone", () => {
    expect(scopeUserDirectory(org)).toHaveLength(listUserDirectory().length);
  });

  it("gives a Business Unit Admin the whole organisation to READ", () => {
    // They borrow contributors across units, and the borrow dialog identifies
    // a person by email because it cannot offer a picker. A directory that
    // stopped at their own boundary left that address unfindable anywhere in
    // the product. Containment lives on the write, not here.
    const seen = scopeUserDirectory(buAdmin);
    expect(seen).toHaveLength(listUserDirectory().length);
    // Other units' admins and people are visible…
    expect(seen.some((e) => e.displayName === "Marcus Reyes")).toBe(true);
    expect(seen.some((e) => e.displayName === "Sofia Rossi")).toBe(true);
    // …and still named under the unit that actually owns them, so an org-wide
    // read never reads as "these people are mine".
    expect(seen.find((e) => e.displayName === "Marcus Reyes")!.businessUnitName).toBe("Payments");
  });

  it("narrows a Project Admin to the units they are part of", () => {
    const seen = scopeUserDirectory(projectAdmin);
    expect(seen.length).toBeGreaterThan(0);
    expect(seen.length).toBeLessThan(listUserDirectory().length);

    // Noah runs Platform Engineering, so he is in view…
    expect(seen.some((e) => e.displayName === "Noah Bennett")).toBe(true);
    // …and Payments' and Lending's people are not.
    expect(seen.some((e) => e.displayName === "Marcus Reyes")).toBe(false);
    expect(seen.some((e) => e.displayName === "Sofia Rossi")).toBe(false);
  });

  it("shows a Project Admin only the unit they BELONG to, not every unit their projects sit in", () => {
    // Priya is a member of Payments and runs projects in Payments AND Lending.
    // Her access scope lists both units — the second is read context so a
    // project page can name its own unit — and reading the directory through
    // that set handed her Lending's people, which is not a roster she is part
    // of. Membership is what decides; a project in a unit is not one.
    const priya = {
      isOrgWide: false,
      businessUnitIds: ["ws_payments", "ws_lending"],
      managedBusinessUnitIds: [],
      actingBindings: [
        { kind: "business_unit", scopeId: "ws_payments" },
        { kind: "project", scopeId: "core-ledger" },
        { kind: "project", scopeId: "payments-api" },
      ],
    };
    const seen = scopeUserDirectory(priya);

    expect(
      seen.filter((e) => e.orgRole !== "org_admin").every((e) => e.businessUnitName === "Payments"),
    ).toBe(true);
    expect(
      seen.some((e) => e.displayName === "Sofia Rossi"),
      "a Lending member leaked",
    ).toBe(false);
    expect(seen.some((e) => e.displayName === "Priya Menon")).toBe(true);
  });

  it("falls back to read context for someone seated on a project with no unit", () => {
    // Read context is all they have; an honest-but-blank directory would be
    // worse than the wider list.
    const seatedOnly = {
      isOrgWide: false,
      businessUnitIds: [PLATFORM_ENG],
      managedBusinessUnitIds: [],
      actingBindings: [{ kind: "project", scopeId: "recon-bots" }],
    };
    expect(scopeUserDirectory(seatedOnly).length).toBeGreaterThan(0);
  });

  it("matches on unit MEMBERSHIP, never on project work", () => {
    // Omar's unit is Platform Engineering; he is a Developer on core-ledger,
    // which is a LENDING project. A Lending-scoped viewer must not get him:
    // his row would print "Platform Engineering" next to his name, which is
    // the other-unit member the narrowing exists to keep out.
    const lending = {
      isOrgWide: false,
      businessUnitIds: ["ws_lending"],
      managedBusinessUnitIds: [],
    };
    const seen = scopeUserDirectory(lending);

    const omar = seen.find((e) => e.displayName === "Omar Nasser");
    expect(omar, "a Platform Engineering member leaked into Lending's list").toBeUndefined();
    // Everyone who IS in view belongs to Lending — no row names a unit the
    // viewer has no part in.
    for (const e of seen) {
      const units = e.bindings.filter((b) => b.scope === "business_unit").map((b) => b.id);
      expect(units, `${e.displayName} is in view without a Lending membership`).toContain(
        "ws_lending",
      );
    }
    // …and the people who genuinely hold a Lending membership are still there.
    expect(seen.some((e) => e.displayName === "Sofia Rossi")).toBe(true);
    expect(seen.some((e) => e.displayName === "Lena Fischer")).toBe(true);
  });

  it("keeps the Organization Admin visible but unplaced, for every viewer", () => {
    // He holds a membership row in every unit, so the shared-unit relabelling
    // found one and printed the VIEWER'S unit beside his name — a Business Unit
    // Admin's roster claiming the Organization Admin as one of their people.
    // Visible, because org-wide authority is a fact about the organisation the
    // unit sits in. Unplaced, because no unit owns him.
    const seenByPlatform = scopeUserDirectory(projectAdmin).find(
      (e) => e.displayName === "Sarthak Kapoor",
    )!;
    expect(seenByPlatform, "the Organization Admin vanished from a scoped list").toBeDefined();
    expect(seenByPlatform.businessUnitName).toBeNull();
    expect(seenByPlatform.orgRole).toBe("org_admin");

    // Org-wide agrees, and always did.
    const orgWide = scopeUserDirectory(org).find((e) => e.displayName === "Sarthak Kapoor")!;
    expect(orgWide.businessUnitName).toBeNull();
  });

  it("hides an unplaced person from every scoped viewer", () => {
    // A Business Unit Admin appointed before a unit was chosen matches no
    // unit — and "matched nothing" must not read as "visible to everyone".
    const { body } = onboardIntoOrganization({
      email: "unplaced-scope@example.test",
      displayName: "Unplaced Scope",
      role: "bu_admin",
    });
    const id = (body as { identityId: string }).identityId;

    expect(scopeUserDirectory(org).some((e) => e.identityId === id)).toBe(true);
    expect(scopeUserDirectory(projectAdmin).some((e) => e.identityId === id)).toBe(false);
  });
});

describe("what a Contributor sees in the sidebar", () => {
  it("nothing — the role holds no job yet", () => {
    // Four working links to four empty pages reads as a broken platform rather
    // than a pending assignment; the utility rail is what stays reachable.
    expect(visibleNav([...ROLE_PERMISSIONS.contributor], { role: "contributor" })).toEqual([]);
  });

  it("but every other delivery role keeps its pages", () => {
    expect(
      visibleNav([...ROLE_PERMISSIONS.developer], { role: "developer" }).length,
    ).toBeGreaterThan(0);
  });
});

describe("changing an appointment", () => {
  it("keeps the role the unit's admin already assigned", () => {
    const { body } = onboardIntoOrganization({
      email: "reassigned@example.test",
      displayName: "Reassigned Person",
      workspaceId: PLATFORM_ENG,
      role: "contributor",
    });
    const identityId = (body as { identityId: string }).identityId;
    setMembershipRole(PLATFORM_ENG, identityId, "qa");

    // The Org Admin confirming where they sit must not silently demote them
    // back to the placeholder.
    const { status } = changeOrgAppointment({
      userId: identityId,
      role: "contributor",
      workspaceId: PLATFORM_ENG,
    });
    expect(status).toBe(200);

    const entry = listUserDirectory().find((e) => e.identityId === identityId);
    expect(entry!.bindings.map((b) => b.role)).toContain("qa");
    expect(entry!.awaitingRole).toBe(false);
  });

  it("refuses a Business Unit role here too", () => {
    const { status } = changeOrgAppointment({
      userId: "idn_tomas",
      role: "architect",
      workspaceId: PLATFORM_ENG,
    });
    expect(status).toBe(422);
  });
});

describe("custom-role ownership", () => {
  const unitAdmin = { isOrgWide: false, managedBusinessUnitIds: [PLATFORM_ENG] };
  const orgAdmin = { isOrgWide: true, managedBusinessUnitIds: [] };

  it("pins a unit admin's role to their own unit, whatever the request says", () => {
    expect(resolveRoleOwner(unitAdmin, null)).toEqual({ businessUnitId: PLATFORM_ENG });
    expect(resolveRoleOwner(unitAdmin, "ws_payments")).toHaveProperty("error");
  });

  it("lets the Organization Admin define an org-wide role", () => {
    expect(resolveRoleOwner(orgAdmin, null)).toEqual({ businessUnitId: null });
  });

  it("stops a unit admin editing another unit's role, or the org-wide one", () => {
    const own = { businessUnitId: PLATFORM_ENG } as CustomRole;
    const other = { businessUnitId: "ws_payments" } as CustomRole;
    const orgWide = { businessUnitId: null } as CustomRole;

    expect(canWriteCustomRole(unitAdmin, own)).toBe(true);
    expect(canWriteCustomRole(unitAdmin, other)).toBe(false);
    expect(canWriteCustomRole(unitAdmin, orgWide)).toBe(false);
    expect(canWriteCustomRole(orgAdmin, other)).toBe(true);
  });
});
