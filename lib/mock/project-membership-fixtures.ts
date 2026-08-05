/**
 * Project-scoped role assignment — an in-memory store (mirrors the
 * mutable-MEMBERSHIPS pattern in workspace-fixtures.ts), so contributors
 * assigned at project creation or from the project Members page persist for
 * the life of the dev process. Plain data + functions, server-safe (imported
 * by the app/api/projects/[id]/members route handlers). This is the
 * DUMMY-DATA source; a real backend project-membership service replaces the
 * route-handler bodies, not these shapes.
 */
import type { ProjectMember } from "@/lib/schemas/project-membership";
import { findOrCreateIdentity, getIdentity } from "./workspace-fixtures";

interface ProjectMembershipRow {
  id: string;
  projectId: string;
  identityId: string;
  role: string;
  status: "active" | "invited" | "deactivated";
  addedAt: string;
}

/**
 * Seeded project bindings.
 *
 * A role is a binding of (person, scope, role) — so the roster below
 * deliberately exercises the cross-scope cases the Users page exists to make
 * legible, rather than giving each person one role everywhere:
 *
 *  - **Marcus Reyes** is `bu_admin` of Payments, yet `project_admin` on a
 *    Lending project and `ba` on another. Governance in one scope, delivery
 *    in two others — legitimate, because separation of duties is a per-scope
 *    rule (`lib/roles.ts::scopeTierConflicts`). He never governs the projects
 *    he delivers on.
 *  - **Farah Haddad** is the same shape the other way round: `bu_admin` of
 *    Lending, `project_admin` on a Platform Engineering project.
 *  - **Priya Menon** is `project_admin` on projects in TWO Business Units
 *    (Payments and Lending) and `ba` on a third. Multi-unit administration is
 *    the case the scoped pages have to answer without an "active" unit to
 *    inherit; her `ba` binding keeps "authorized on" and "administers"
 *    visibly different at the same time.
 *  - **Diego Alvarez** carries a different delivery role per project
 *    (`developer` on two, `architect` on a third), matching his differing
 *    Business Unit bindings in workspace-fixtures.ts.
 *
 * Project ids are the seeded PROJECTS in mocks/fixtures.ts; identity ids are
 * the IDENTITIES in workspace-fixtures.ts. Keep both in sync if either moves.
 */
const SEEDED: { projectId: string; identityId: string; role: string }[] = [
  // Lending — mobile onboarding journey
  { projectId: "mobile-onboarding", identityId: "idn_marcus", role: "project_admin" },
  { projectId: "mobile-onboarding", identityId: "idn_priya", role: "ba" },
  { projectId: "mobile-onboarding", identityId: "idn_diego", role: "developer" },
  { projectId: "mobile-onboarding", identityId: "idn_wei", role: "qa" },

  // Lending — core ledger modernization
  // Priya administers this one AND payments-api, in two different Business
  // Units. That is the multi-unit admin case: with no Business Unit switcher
  // in the chrome ([[no-bu-switcher-in-chrome]]) the scoped pages union their
  // reads across both units and ask which one a credential belongs to. Without
  // a seeded person shaped like this the whole path is unreachable, and
  // working code reads as untested — see [[scoped-fixtures-need-coverage]].
  // This project also had no project_admin at all, which was its own gap.
  { projectId: "core-ledger", identityId: "idn_priya", role: "project_admin" },
  { projectId: "core-ledger", identityId: "idn_diego", role: "architect" },
  { projectId: "core-ledger", identityId: "idn_marcus", role: "ba" },
  { projectId: "core-ledger", identityId: "idn_lena", role: "devops_engineer" },

  // Payments — SCA exemption defect
  { projectId: "payments-api", identityId: "idn_priya", role: "project_admin" },
  { projectId: "payments-api", identityId: "idn_diego", role: "developer" },
  { projectId: "payments-api", identityId: "idn_wei", role: "qa" },

  // Payments — fraud feature store
  { projectId: "fraud-features", identityId: "idn_lena", role: "data_engineer" },
  { projectId: "fraud-features", identityId: "idn_wei", role: "qa" },

  // Platform Engineering — reconciliation bots
  { projectId: "recon-bots", identityId: "idn_farah", role: "project_admin" },
  { projectId: "recon-bots", identityId: "idn_lena", role: "devops_engineer" },

  // Noah runs Platform Engineering AND builds on a Lending project.
  //
  // Seeded to make the shape of the rule visible: a person administers exactly
  // one Business Unit, and is free to be a contributor anywhere else. Without
  // a case like this the directory only ever showed admins confined to their
  // own unit, and the allowed half of `buAdminElsewhere` looked like a rule
  // nothing exercises ([[scoped-fixtures-need-coverage]]).
  { projectId: "core-ledger", identityId: "idn_noah", role: "developer" },
];

let nextId = 1;
const PROJECT_MEMBERSHIPS: ProjectMembershipRow[] = SEEDED.map((s) => ({
  id: `pm_seed_${nextId++}`,
  projectId: s.projectId,
  identityId: s.identityId,
  role: s.role,
  status: "active" as const,
  addedAt: "2026-03-02T09:00:00.000Z",
}));

function toProjectMember(row: ProjectMembershipRow): ProjectMember | null {
  const identity = getIdentity(row.identityId);
  if (!identity) return null;
  return {
    membershipId: row.id,
    projectId: row.projectId,
    identity,
    role: row.role,
    status: row.status,
    addedAt: row.addedAt,
  };
}

export function listProjectMembers(projectId: string): ProjectMember[] {
  return PROJECT_MEMBERSHIPS.filter((r) => r.projectId === projectId)
    .map(toProjectMember)
    .filter((m): m is ProjectMember => m !== null);
}

/** Every (project, role) binding an identity holds, across all projects —
 *  the cross-scope join the Users page's detail view needs. */
export function listProjectMembershipsForIdentity(
  identityId: string,
): { projectId: string; role: string; status: ProjectMembershipRow["status"] }[] {
  return PROJECT_MEMBERSHIPS.filter((r) => r.identityId === identityId).map((r) => ({
    projectId: r.projectId,
    role: r.role,
    status: r.status,
  }));
}

/**
 * Add a contributor to a project by email — resolving to an existing person
 * or minting a brand-new one via `findOrCreateIdentity` (the shared
 * onboarding primitive), so an unrecognized email never fails this call.
 * A genuinely new person starts `"invited"`; a recognized one starts
 * `"active"`.
 */
export function addProjectMember(
  projectId: string,
  input: { email: string; displayName?: string; roleName: string },
): ProjectMember {
  const identity = findOrCreateIdentity(input.email, input.displayName);
  // findOrCreateIdentity stamps a "pending|" ssoSubject only on identities it
  // just minted — the reliable signal that this is a genuinely new person,
  // as opposed to an existing person joining this project for the first time.
  const isNewPerson = identity.ssoSubject.startsWith("pending|");

  const row: ProjectMembershipRow = {
    id: `pm_${nextId++}`,
    projectId,
    identityId: identity.id,
    role: input.roleName,
    status: isNewPerson ? "invited" : "active",
    addedAt: new Date().toISOString(),
  };
  PROJECT_MEMBERSHIPS.push(row);
  return toProjectMember(row)!;
}

export function updateProjectMemberRole(
  projectId: string,
  membershipId: string,
  roleName: string,
): ProjectMember | undefined {
  const row = PROJECT_MEMBERSHIPS.find((r) => r.projectId === projectId && r.id === membershipId);
  if (!row) return undefined;
  row.role = roleName;
  return toProjectMember(row) ?? undefined;
}

export function removeProjectMember(projectId: string, membershipId: string): boolean {
  const i = PROJECT_MEMBERSHIPS.findIndex(
    (r) => r.projectId === projectId && r.id === membershipId,
  );
  if (i === -1) return false;
  PROJECT_MEMBERSHIPS.splice(i, 1);
  return true;
}
