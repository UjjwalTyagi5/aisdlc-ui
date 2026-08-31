/**
 * The org-level onboarding transaction, in one place.
 *
 * WHY A SHARED FUNCTION rather than two handlers. This write touches four
 * stores — the identity roster, the Business Unit memberships, the org-level
 * appointment, and the notification queue — and it has to run identically in
 * the Next route handler and in the MSW handler, which are separate runtimes
 * with separate copies of all four ([[msw-dual-runtime-mutation-rule]]). Two
 * hand-kept copies of a four-store write is a divergence waiting to happen, and
 * the divergence would show up as "the Business Unit Admin was never told",
 * which nobody would think to look for.
 *
 * DUMMY-DATA SEAM: a real backend owns this transaction and both handlers
 * become proxies. The shape it returns is the contract
 * (`lib/schemas/onboarding.ts::OnboardingResult`).
 */
import {
  isOrgAssignableRole,
  isUnitAssignableRole,
  ROLE_META,
  type PlatformRole,
} from "@/lib/roles";
import { BUSINESS_UNIT_LABEL } from "@/lib/scope";

import { addAccessMember } from "./access-fixtures";
import { completeRoleAssignment, createGovernanceApproval } from "./governance-approval-fixtures";
import { emitNotification } from "./notification-fixtures";
import { getOrgRole, setOrgRole, type OrgRole } from "./org-role-fixtures";
import { removeProjectMembershipsInWorkspace } from "./project-membership-fixtures";
import {
  buAdminIdentityIdsFor,
  createMembership,
  findOrCreateIdentity,
  getIdentity,
  getIdentityBySsoSubject,
  getWorkspace,
  listMembershipsForIdentity,
  removeMembership,
  setMembershipRole,
} from "./workspace-fixtures";

/**
 * Tell a unit's admins that someone is sitting in their unit with no role.
 *
 * Shared by both writes below, because the notification is not a nicety
 * attached to onboarding — it is the only thing that carries the handover. A
 * person moved into a unit by an appointment change is in exactly the same
 * position as one onboarded into it: holding a placeholder, waiting on an
 * admin who has no other way of knowing.
 *
 * Returns whether anyone was actually told. A unit with no admin appointed
 * silently swallows the handover otherwise, and the caller should say so.
 */
function notifyAwaitingRole(
  workspaceId: string,
  person: { id: string; displayName: string; email: string | null; ssoSubject: string },
  raisedBy: string,
): boolean {
  const unitName = getWorkspace(workspaceId)?.displayName ?? workspaceId;
  const href = `/users?bu=${encodeURIComponent(workspaceId)}&awaiting=1`;

  // The queue item. It lands in Requests & Approvals beside every other thing
  // waiting on this admin, which is where they look for work they owe someone;
  // the pending list on Users is the same obligation seen from the person's
  // side. Both close together, because both point at one request
  // (`completeRoleAssignment`).
  createGovernanceApproval({
    type: "role_assignment",
    workspaceId,
    workspaceName: unitName,
    title: `Role needed: ${person.displayName} in ${unitName}`,
    summary: `${person.displayName} was onboarded as a ${ROLE_META.contributor.label} and holds no permissions until you assign a role.`,
    description: `${person.displayName}${person.email ? ` (${person.email})` : ""} was placed in ${unitName} by the ${ROLE_META.org_admin.label}. Assign a built-in role or one you compose for this ${BUSINESS_UNIT_LABEL.toLowerCase()}; until then they can sign in and do nothing.`,
    requestedBy: raisedBy,
    requestedByRole: "org_admin",
    targetRef: String(person.id),
    payload: { identityId: String(person.id), userId: person.ssoSubject, displayName: person.displayName },
    // Suppressed in favour of the addressed message below — see the `notify`
    // docblock in governance-approval-fixtures.ts.
    notify: false,
  });

  let notified = false;
  for (const admin of buAdminIdentityIdsFor(workspaceId)) {
    emitNotification({
      kind: "member_awaiting_role",
      title: `${person.displayName} needs a role in ${unitName}`,
      body: `They were onboarded as a ${ROLE_META.contributor.label} and hold no permissions until you assign one.`,
      href,
      identityId: admin,
    });
    notified = true;
  }
  return notified;
}

export interface OnboardingInput {
  email?: string;
  displayName?: string;
  workspaceId?: string | null;
  role?: string;
  /**
   * Units the caller ADMINISTERS, or null meaning org-wide.
   *
   * Mirrors the two branches in shared/routers/onboarding.py. Without it this
   * fixture answered every request as if an Organization Admin had made it, so mock
   * mode showed a Business Unit Admin a 422 for the one flow that is now theirs.
   */
  administeredUnitIds?: string[] | null;
  /** Who is doing this, for the request's "raised by" line. Supplied by the
   *  route handler from the session — never by the browser. */
  actorName?: string;
}

export interface OnboardingOutcome {
  status: number;
  body: unknown;
}

/**
 * Admit a person to the organisation with one of the two org-level roles.
 *
 * The validation here is the real gate, not the dialog's. A picker that offers
 * two options is a convenience; a request that arrives naming `developer`
 * because someone kept an old client open, or curled it, must be refused for
 * the same reason the picker doesn't offer it — an Org Admin does not decide
 * what people do inside a unit.
 */
export function onboardIntoOrganization(input: OnboardingInput): OnboardingOutcome {
  const { email, displayName, role } = input;
  const workspaceId = input.workspaceId || null;
  const actor = input.actorName?.trim() || ROLE_META.org_admin.label;
  const administered = input.administeredUnitIds ?? null;
  const orgWide = administered === null;

  if (!email || !role) {
    return {
      status: 422,
      body: { code: "invalid_input", message: "email and role are required" },
    };
  }

  if (orgWide) {
    if (!isOrgAssignableRole(role)) {
      return {
        status: 422,
        body: {
          code: "invalid_role",
          message: `Onboarding assigns ${ROLE_META.bu_admin.label} or ${ROLE_META.contributor.label}. Every other role is granted by a ${BUSINESS_UNIT_LABEL.toLowerCase()}'s admin.`,
        },
      };
    }

    // A contributor with no unit belongs to nobody: no admin is prompted for
    // their role, so they would sit with no access and nothing to explain why.
    if (role === "contributor" && !workspaceId) {
      return {
        status: 422,
        body: {
          code: "invalid_input",
          message: `A ${ROLE_META.contributor.label} needs a ${BUSINESS_UNIT_LABEL.toLowerCase()} — its admin is who gives them a role.`,
        },
      };
    }
  } else {
    // The Business Unit Admin's branch. Mirrors the server exactly, including the
    // 404 — a unit you do not administer is not confirmed to exist by the error.
    if (!workspaceId) {
      return {
        status: 422,
        body: {
          code: "unit_required",
          message: `Choose the ${BUSINESS_UNIT_LABEL.toLowerCase()} to onboard them into.`,
        },
      };
    }
    if (!administered.includes(String(workspaceId))) {
      return { status: 404, body: { code: "not_found", message: "Unknown workspace" } };
    }
    if (!isUnitAssignableRole(role)) {
      return {
        status: 422,
        body: {
          code: "invalid_role",
          message: `Choose the role this person will hold in your ${BUSINESS_UNIT_LABEL.toLowerCase()}. ${ROLE_META.bu_admin.label} is an organization-level appointment, and ${ROLE_META.contributor.label} would file a request back to you.`,
        },
      };
    }
  }

  if (workspaceId && !getWorkspace(workspaceId)) {
    return { status: 404, body: { code: "not_found", message: "Unknown workspace" } };
  }

  const identity = findOrCreateIdentity(email, displayName);
  // The org-role store records the ORG-level standing. A unit role is not one, so a
  // scoped onboarding leaves the person a contributor organisationally and carries
  // the real role on the unit membership below — which is what the directory reads.
  setOrgRole(identity.id, (orgWide ? role : "contributor") as OrgRole, workspaceId);

  let notified = false;
  if (workspaceId) {
    const membership = createMembership(workspaceId, identity.id, role);
    // Keep the Roles & Access screen's separate member list (ACCESS_MEMBERS)
    // in sync — it doesn't read from workspace-fixtures.ts.
    addAccessMember(
      workspaceId,
      { userId: identity.ssoSubject, name: identity.displayName, email: identity.email },
      membership.role,
    );

    // The handover. A Contributor arrives with a placeholder and no permissions;
    // the only person who can turn that into a job is this unit's admin, and
    // nothing else in the product would ever tell them it happened.
    if (role === "contributor") {
      notified = notifyAwaitingRole(workspaceId, identity, actor);
    }
  }

  return {
    status: 201,
    body: {
      identityId: identity.id,
      email: identity.email,
      displayName: identity.displayName,
      initials: identity.initials,
      workspaceId,
      role: role as PlatformRole,
      membershipStatus: workspaceId ? ("invited" as const) : null,
      notifiedBusinessUnitAdmin: notified,
    },
  };
}

/**
 * Give someone a role inside a Business Unit — the Business Unit Admin's half
 * of onboarding, and the only write that discharges a `role_assignment`.
 *
 * ONE FUNCTION FOR TWO SURFACES. The admin can act from the pending list on
 * Users or from the row in Requests & Approvals, and both must leave the same
 * three things true: the membership carries the new role, the request is
 * closed, and the person is told. Wiring the request's closure to the button
 * instead of to the write would mean the OTHER surface left it open — a queue
 * item for work that was already done, which is the failure people learn to
 * ignore queues over.
 *
 * `roleLabel` is passed in rather than resolved here because a custom role's
 * name lives in the roles store; the caller already has it, and a second
 * lookup would print a raw `role_3` in the audit trail when it missed.
 */
export function assignBusinessUnitRole(input: {
  workspaceId: string;
  /** SSO subject or identity id. */
  userId: string;
  roleName: string;
  roleLabel?: string;
  actorName?: string;
}): OnboardingOutcome {
  const identity = getIdentity(input.userId) ?? getIdentityBySsoSubject(input.userId);
  if (!identity) return { status: 404, body: { code: "not_found", message: "Unknown person" } };

  const workspace = getWorkspace(input.workspaceId);
  if (!workspace) return { status: 404, body: { code: "not_found", message: "Unknown workspace" } };

  if (!input.roleName) {
    return { status: 422, body: { code: "invalid_input", message: "roleName is required" } };
  }
  // The placeholder is not a role. Offering it back would let an admin answer
  // "what does this person do" with "still nothing", which is the state they
  // were asked to resolve.
  if (input.roleName === "contributor") {
    return {
      status: 422,
      body: {
        code: "invalid_role",
        message: `${ROLE_META.contributor.label} is what they hold now — pick the role they should have.`,
      },
    };
  }

  const membership = setMembershipRole(input.workspaceId, identity.id, input.roleName);
  addAccessMember(
    input.workspaceId,
    { userId: identity.ssoSubject, name: identity.displayName, email: identity.email },
    input.roleName,
  );

  const label = input.roleLabel?.trim() || input.roleName;
  const actor = input.actorName?.trim() || ROLE_META.bu_admin.label;
  const closed = completeRoleAssignment(input.workspaceId, String(identity.id), label, actor);

  // The person waiting. They were told nothing when they were onboarded —
  // there was nothing to tell them — and this is the moment the account they
  // already have starts being able to do something.
  emitNotification({
    kind: "request_approved",
    title: `You are now ${label} in ${workspace.displayName}`,
    body: `${actor} assigned your role.`,
    href: "/my-access",
    identityId: String(identity.id),
  });

  return {
    status: 200,
    body: {
      userId: identity.ssoSubject,
      identityId: identity.id,
      email: identity.email,
      displayName: identity.displayName,
      initials: identity.initials,
      roleName: membership.role,
      workspaceId: input.workspaceId,
      /** The request this closed, if the assignment was one that was owed. */
      resolvedRequestId: closed?.id ?? null,
      joinedAt: new Date().toISOString(),
    },
  };
}

/**
 * Change an existing person's org-level appointment — the Organization Admin
 * correcting a decision they already made, or finally giving an unassigned
 * Business Unit Admin a unit.
 *
 * SCOPED TO THE SAME TWO ROLES as onboarding, for the same reason: this is the
 * Org Admin's lever, and widening it here would put back the delivery-role
 * dropdown that the redesign removed from the other end.
 */
export function changeOrgAppointment(input: {
  /** SSO subject or identity id — the directory hands back both. */
  userId: string;
  role?: string;
  workspaceId?: string | null;
  actorName?: string;
}): OnboardingOutcome {
  const identity = getIdentity(input.userId) ?? getIdentityBySsoSubject(input.userId);
  if (!identity) return { status: 404, body: { code: "not_found", message: "Unknown person" } };
  const actor = input.actorName?.trim() || ROLE_META.org_admin.label;

  const role = input.role;
  if (!role || !isOrgAssignableRole(role)) {
    return {
      status: 422,
      body: {
        code: "invalid_role",
        message: `An appointment is ${ROLE_META.bu_admin.label} or ${ROLE_META.contributor.label}.`,
      },
    };
  }
  const workspaceId = input.workspaceId || null;
  if (role === "contributor" && !workspaceId) {
    return {
      status: 422,
      body: {
        code: "invalid_input",
        message: `A ${ROLE_META.contributor.label} needs a ${BUSINESS_UNIT_LABEL.toLowerCase()}.`,
      },
    };
  }
  if (workspaceId && !getWorkspace(workspaceId)) {
    return { status: 404, body: { code: "not_found", message: "Unknown workspace" } };
  }

  const previous = getOrgRole(identity.id);
  setOrgRole(identity.id, role as OrgRole, workspaceId);

  let notified = false;
  let leftProjects = 0;
  if (workspaceId) {
    const held = listMembershipsForIdentity(identity.id).find(
      (m) => String(m.workspaceId) === workspaceId,
    );

    /**
     * A person belongs to ONE Business Unit, so moving them is a move and not
     * an addition.
     *
     * Their project seats do not travel with them: those projects belong to the
     * unit being left, are funded from its budget and governed by its admin.
     * Leaving either the old membership or its seats in place would produce the
     * cross-unit member that `projectMembershipBlock` refuses to create in the
     * first place — arrived at through the back door of an appointment change.
     *
     * The Organization Admin is exempt: their rows in every unit are not a
     * membership so much as the shape their org-wide authority takes here.
     */
    if (previous?.role !== "org_admin") {
      for (const m of listMembershipsForIdentity(identity.id)) {
        const from = String(m.workspaceId);
        if (from === workspaceId) continue;
        leftProjects += removeProjectMembershipsInWorkspace(String(identity.id), from);
        removeMembership(from, String(identity.id));
      }
    }

    if (role === "bu_admin") {
      // An appointment IS the decision, so it overwrites whatever they held in
      // this unit — `setMembershipRole` also marks it active, since an admin
      // waiting to accept an invitation is a unit with no admin.
      setMembershipRole(workspaceId, identity.id, "bu_admin");
    } else if (!held) {
      // Moved into a unit they did not belong to: the placeholder, and the
      // handover that goes with it.
      createMembership(workspaceId, identity.id, "contributor");
      notified = notifyAwaitingRole(workspaceId, identity, actor);
    }
    // A contributor who ALREADY holds a role in this unit keeps it. The Org
    // Admin was confirming where they sit, not revoking the job their unit's
    // admin gave them — and silently demoting someone to a placeholder is the
    // kind of change nobody would think to look for.

    addAccessMember(
      workspaceId,
      { userId: identity.ssoSubject, name: identity.displayName, email: identity.email },
      role === "bu_admin" ? "bu_admin" : (held?.role ?? "contributor"),
    );
  }

  return {
    status: 200,
    body: {
      identityId: identity.id,
      email: identity.email,
      displayName: identity.displayName,
      initials: identity.initials,
      workspaceId,
      role,
      previousRole: previous?.role ?? null,
      /** Project seats dropped by the move — the caller says so rather than
       *  taking someone off three projects silently. */
      leftProjects,
      membershipStatus: null,
      notifiedBusinessUnitAdmin: notified,
    },
  };
}
