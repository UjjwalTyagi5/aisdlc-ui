/**
 * Borrowing a contributor from another Business Unit.
 *
 * THE SHAPE OF THE DEAL. A contributor belongs to one Business Unit — their
 * parent — and that never changes: their persona, their ownership and the admin
 * who manages them all stay there. A second unit that needs them on a project
 * cannot simply take them; it asks their parent unit's admin, names the project
 * and the role, and waits. On approval the contributor gains exactly one
 * project in the borrowing unit and nothing else — not its other projects, not
 * its budget, connectors, models or people (`resolveAccessScope` is what makes
 * that last part true, by refusing to inherit a unit from a project binding).
 *
 * WHY THE PARENT ADMIN AND NOT THE BORROWING ONE. The borrowing unit already
 * wants this; asking it to approve its own want is not an approval. The person
 * being lent is the parent unit's headcount, accountable to its admin and
 * counted against its budget, so that admin is the only one whose "yes" means
 * anything. This is also why the request routes SIDEWAYS rather than up the
 * usual ladder — see `lib/requests/routing.ts::TYPE_ROUTED`.
 *
 * Both halves live here for the same reason as `lib/mock/onboarding.ts`: the
 * write touches four stores and has to run identically in the Next route
 * handler and the MSW handler ([[msw-dual-runtime-mutation-rule]]).
 */
import { isCrossBuAssignableRole, ROLE_META, type PlatformRole } from "@/lib/roles";
import { BUSINESS_UNIT_LABEL } from "@/lib/scope";
import type { GovernanceApproval } from "@/lib/schemas/governance-approval";

import { grantCrossBuAccess, hasCrossBuGrant } from "./cross-bu-fixtures";
import {
  createGovernanceApproval,
  listGovernanceApprovals,
} from "./governance-approval-fixtures";
import { emitNotification } from "./notification-fixtures";
import { getProjectById } from "./project-fixtures";
import {
  listProjectMembers,
  parentBusinessUnitOf,
  seatProjectMember,
} from "./project-membership-fixtures";
import { buAdminIdentityIdsFor, getIdentityByEmail, getWorkspace } from "./workspace-fixtures";

export interface CrossBuOutcome {
  status: number;
  body: unknown;
}

/** The open request for this person on this project, if one is already in
 *  flight — so a second ask doesn't queue behind the first. */
export function findOpenCrossBuRequest(
  identityId: string,
  projectId: string,
): GovernanceApproval | undefined {
  const OPEN = new Set(["submitted", "pending_review", "escalated"]);
  return listGovernanceApprovals().find(
    (a) =>
      a.type === "cross_bu_assignment" &&
      OPEN.has(a.status) &&
      a.projectId === String(projectId) &&
      (a.payload?.identityId as string | undefined) === String(identityId),
  );
}

/**
 * Ask a contributor's own Business Unit Admin to lend them to your project.
 *
 * IDENTIFIED BY EMAIL, on purpose. The asker cannot see the other unit's people
 * — the directory is scoped, and widening it so they could pick from a list
 * would leak the roster this whole flow exists to keep behind an approval. They
 * already know who they want; typing the address tells them nothing they did
 * not already believe, and an address that resolves to nobody is refused
 * without confirming anything either way beyond that.
 */
export function requestCrossBuAssignment(input: {
  projectId: string;
  email?: string;
  roleName?: string;
  reason?: string;
  actorName: string;
  actorIdentityId: string | null;
  actorRole: PlatformRole | null;
}): CrossBuOutcome {
  const project = getProjectById(input.projectId);
  if (!project) return { status: 404, body: { code: "not_found", message: "Unknown project" } };

  const targetWorkspaceId = project.workspaceId ? String(project.workspaceId) : null;
  if (!targetWorkspaceId) {
    return {
      status: 422,
      body: { code: "invalid_input", message: "That project belongs to no business unit." },
    };
  }

  if (!input.email || !input.roleName) {
    return {
      status: 422,
      body: { code: "invalid_input", message: "An email and a role are required." },
    };
  }

  if (!isCrossBuAssignableRole(input.roleName)) {
    return {
      status: 422,
      body: {
        code: "invalid_role",
        message: `Only project roles can be borrowed. ${ROLE_META.org_admin.label} and ${ROLE_META.bu_admin.label} are never assigned across ${BUSINESS_UNIT_LABEL.toLowerCase()}s.`,
      },
    };
  }

  const identity = getIdentityByEmail(input.email);
  // Not minted. Lending presupposes someone who already belongs to a unit —
  // creating a person here would invent the very thing being asked for, and
  // hand the asker a contributor with no parent admin to approve it.
  if (!identity) {
    return {
      status: 404,
      body: {
        code: "not_found",
        message: `Nobody on the platform uses ${input.email}. Ask your ${BUSINESS_UNIT_LABEL.toLowerCase()}'s admin to onboard them instead.`,
      },
    };
  }

  const parentWorkspaceId = parentBusinessUnitOf(String(identity.id));
  if (!parentWorkspaceId) {
    return {
      status: 422,
      body: {
        code: "invalid_input",
        message: `${identity.displayName} belongs to no ${BUSINESS_UNIT_LABEL.toLowerCase()} yet, so there is nobody to ask.`,
      },
    };
  }

  if (parentWorkspaceId === targetWorkspaceId) {
    return {
      status: 422,
      body: {
        code: "invalid_input",
        message: `${identity.displayName} is already in this ${BUSINESS_UNIT_LABEL.toLowerCase()} — add them to the project directly.`,
      },
    };
  }

  if (listProjectMembers(String(project.id)).some((m) => m.identity.id === identity.id)) {
    return {
      status: 409,
      body: { code: "conflict", message: `${identity.displayName} is already on this project.` },
    };
  }

  const open = findOpenCrossBuRequest(String(identity.id), String(project.id));
  if (open) {
    return {
      status: 409,
      body: {
        code: "conflict",
        message: `A request for ${identity.displayName} on this project is already with ${getWorkspace(parentWorkspaceId)?.displayName ?? "their admin"}.`,
      },
    };
  }

  const parentName = getWorkspace(parentWorkspaceId)?.displayName ?? parentWorkspaceId;
  const targetName = getWorkspace(targetWorkspaceId)?.displayName ?? targetWorkspaceId;
  const roleLabel = ROLE_META[input.roleName as PlatformRole]?.label ?? input.roleName;

  const created = createGovernanceApproval({
    type: "cross_bu_assignment",
    // The PARENT unit, not the project's. This is what lands the request in the
    // right admin's queue — the queue filters by the unit it names.
    workspaceId: parentWorkspaceId,
    workspaceName: parentName,
    projectId: String(project.id),
    projectName: project.name,
    title: `${targetName} asks for ${identity.displayName} as ${roleLabel}`,
    summary: `${input.actorName} is asking to add ${identity.displayName} to ${project.name} (${targetName}) as ${roleLabel}. They stay in ${parentName}.`,
    description:
      input.reason?.trim() ||
      `${project.name} needs a ${roleLabel}. ${identity.displayName} would keep ${parentName} as their ${BUSINESS_UNIT_LABEL.toLowerCase()} and reach this project only.`,
    requestedBy: input.actorName,
    requestedById: input.actorIdentityId,
    requestedByRole: input.actorRole,
    targetRef: String(project.id),
    payload: {
      identityId: String(identity.id),
      userId: identity.ssoSubject,
      displayName: identity.displayName,
      email: identity.email,
      roleName: input.roleName,
      roleLabel,
      parentWorkspaceId,
      parentWorkspaceName: parentName,
      targetWorkspaceId,
      targetWorkspaceName: targetName,
    },
    // Addressed below instead: the generic notification goes to every
    // `bu_admin` in the organisation, and this one is owed by exactly one.
    notify: false,
  });

  let notified = false;
  for (const admin of buAdminIdentityIdsFor(parentWorkspaceId)) {
    emitNotification({
      kind: "request_approval_required",
      title: `${targetName} wants to borrow ${identity.displayName}`,
      body: `As ${roleLabel} on ${project.name}. They stay in ${parentName}.`,
      href: "/approvals",
      identityId: admin,
    });
    notified = true;
  }

  return {
    status: 201,
    body: { ...created, notifiedParentAdmin: notified },
  };
}

/**
 * Apply an approved loan: record the grant, then seat them.
 *
 * IN THAT ORDER, and it matters. `projectMembershipBlock` refuses a cross-unit
 * seat unless a grant already exists, so writing the grant first is what makes
 * the seat legal rather than an exception carved around the rule. The seat is
 * then created through the same guarded path everything else uses.
 */
export function applyCrossBuAssignment(
  approval: GovernanceApproval,
  decidedBy: string,
): void {
  const p = (approval.payload ?? {}) as Record<string, string | undefined>;
  const { identityId, roleName, parentWorkspaceId, targetWorkspaceId } = p;
  if (!identityId || !roleName || !parentWorkspaceId || !targetWorkspaceId) return;
  if (!approval.projectId) return;

  grantCrossBuAccess({
    identityId,
    projectId: approval.projectId,
    parentWorkspaceId,
    targetWorkspaceId,
    role: roleName,
    approvedBy: decidedBy,
    requestId: approval.id,
  });

  seatProjectMember(approval.projectId, identityId, roleName);

  // The person being lent hears about it. Nothing else in the product would
  // tell them a second unit's project has appeared in their list.
  emitNotification({
    kind: "request_approved",
    title: `You joined ${approval.projectName ?? "a project"}`,
    body: `${decidedBy} approved ${p.targetWorkspaceName ?? "another business unit"} borrowing you as ${p.roleLabel ?? roleName}. Your ${BUSINESS_UNIT_LABEL.toLowerCase()} is unchanged.`,
    href: `/projects/${approval.projectId}`,
    identityId,
  });
}

/** True once the loan is live — used by the row to stop offering a decision on
 *  something already applied. */
export function isCrossBuGranted(identityId: string, projectId: string): boolean {
  return hasCrossBuGrant(identityId, projectId);
}
