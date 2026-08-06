/**
 * Project-scoped role assignment — an in-memory store (mirrors the
 * mutable-MEMBERSHIPS pattern in workspace-fixtures.ts), so contributors
 * assigned at project creation or from the project Members page persist for
 * the life of the dev process. Plain data + functions, server-safe (imported
 * by the app/api/projects/[id]/members route handlers). This is the
 * DUMMY-DATA source; a real backend project-membership service replaces the
 * route-handler bodies, not these shapes.
 */
import { ROLE_META, type PlatformRole } from "@/lib/roles";
import { BUSINESS_UNIT_LABEL } from "@/lib/scope";
import type { ProjectMember } from "@/lib/schemas/project-membership";
import { getProjectById } from "./project-fixtures";
import {
  getIdentityByEmail,
  findOrCreateIdentity,
  getIdentity,
  getWorkspace,
  listMembershipsForIdentity,
  setMembershipRole,
} from "./workspace-fixtures";

interface ProjectMembershipRow {
  id: string;
  projectId: string;
  identityId: string;
  role: string;
  status: "active" | "invited" | "deactivated";
  addedAt: string;
  /** Agents granted on top of the role's own reach — see ProjectMember. */
  extraAgents?: string[];
}

/**
 * Seeded project bindings.
 *
 * TWO RULES HOLD THIS ROSTER, and both are enforced at the write path by
 * `projectMembershipBlock` below rather than merely observed here:
 *
 *  1. NO GOVERNANCE-TIER PERSON. An Organization Admin or Business Unit Admin
 *     is never a member of a project. They govern the units projects live in —
 *     approving budget, granting models, appointing the people who deliver —
 *     and a governance role holding a project seat is how the person approving
 *     work ends up on the team doing it.
 *  2. EVERY MEMBER BELONGS TO THE PROJECT'S OWN BUSINESS UNIT. A project is
 *     staffed from the unit that owns it, funds it and governs it. The earlier
 *     roster crossed units freely — Priya administering a Lending project from
 *     Payments, Omar building on one from Platform Engineering — which read on
 *     screen as a Payments person on Lending's team and put both of them in
 *     units they had no membership of.
 *
 * A role is still a binding of (person, scope, role), and the roster still
 * shows it: **Diego Alvarez** is `developer` on one Payments project and
 * `architect` on another, so the same person carries different roles in
 * different scopes — now within the one unit he belongs to.
 *
 * Project ids are the seeded PROJECTS in mocks/fixtures.ts; identity ids are
 * the IDENTITIES in workspace-fixtures.ts, and each one's Business Unit must
 * match the project's. Keep all three in sync if any moves.
 */
const SEEDED: { projectId: string; identityId: string; role: string }[] = [
  // ── Lending: mobile onboarding journey ────────────────────────────────────
  { projectId: "mobile-onboarding", identityId: "idn_yuki", role: "project_admin" },
  { projectId: "mobile-onboarding", identityId: "idn_sofia", role: "ba" },
  { projectId: "mobile-onboarding", identityId: "idn_rafael", role: "architect" },
  { projectId: "mobile-onboarding", identityId: "idn_ingrid", role: "qa" },
  { projectId: "mobile-onboarding", identityId: "idn_lena", role: "devops_engineer" },

  // ── Lending: core ledger modernization ────────────────────────────────────
  { projectId: "core-ledger", identityId: "idn_ana", role: "project_admin" },
  { projectId: "core-ledger", identityId: "idn_rafael", role: "architect" },
  { projectId: "core-ledger", identityId: "idn_sofia", role: "ba" },
  { projectId: "core-ledger", identityId: "idn_lena", role: "devops_engineer" },
  { projectId: "core-ledger", identityId: "idn_ingrid", role: "qa" },

  // ── Payments: SCA exemption defect ────────────────────────────────────────
  { projectId: "payments-api", identityId: "idn_priya", role: "project_admin" },
  { projectId: "payments-api", identityId: "idn_iris", role: "architect" },
  // Developer here, Architect on fraud-features — the same person, two scopes,
  // two roles, one Business Unit.
  { projectId: "payments-api", identityId: "idn_diego", role: "developer" },
  { projectId: "payments-api", identityId: "idn_wei", role: "qa" },
  // The only seeded Security Engineer. Without one, the role's standing audit
  // and trace access — the thing that makes it different from every other
  // contributor — was unreachable from any sign-in.
  { projectId: "payments-api", identityId: "idn_hana", role: "security_engineer" },
  { projectId: "payments-api", identityId: "idn_luca", role: "scrum_master" },

  // ── Payments: fraud feature store ─────────────────────────────────────────
  { projectId: "fraud-features", identityId: "idn_priya", role: "project_admin" },
  { projectId: "fraud-features", identityId: "idn_diego", role: "architect" },
  { projectId: "fraud-features", identityId: "idn_bruno", role: "data_engineer" },
  { projectId: "fraud-features", identityId: "idn_wei", role: "qa" },
  { projectId: "fraud-features", identityId: "idn_hana", role: "security_engineer" },

  // ── Platform Engineering: reconciliation bots ─────────────────────────────
  { projectId: "recon-bots", identityId: "idn_ravi", role: "project_admin" },
  { projectId: "recon-bots", identityId: "idn_omar", role: "developer" },
  { projectId: "recon-bots", identityId: "idn_nadia", role: "devops_engineer" },
  { projectId: "recon-bots", identityId: "idn_sam", role: "qa" },
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
    extraAgents: row.extraAgents,
  };
}

/**
 * Why this person may not join this project, or null if they may.
 *
 * TWO REFUSALS, and they answer different questions.
 *
 * 1. THE GOVERNANCE TIER IS NEVER ON A PROJECT. An Organization Admin or
 *    Business Unit Admin governs the units projects live in — they approve the
 *    budget the project spends, grant the models it uses, and appoint the people
 *    who deliver it. A governance role sitting in the project's own roster is
 *    how the person signing off the work ends up on the team doing it, and no
 *    audit trail can untangle that after the fact.
 *
 * 2. A PROJECT IS STAFFED FROM ITS OWN BUSINESS UNIT. The unit owns the
 *    project, funds it from its budget and governs who works in it, so someone
 *    outside the unit joining it is spending a budget they are not counted
 *    against and answering to an admin who cannot see them. Someone genuinely
 *    needed on another unit's project is onboarded into that unit first — which
 *    is a decision with an owner, rather than a side effect of an email typed
 *    into a picker.
 *
 * Checked here rather than in the picker because the add path takes an EMAIL:
 * a Business Unit Admin's address typed into "add a contributor" would resolve
 * to their real identity and seat them, and no dropdown filter can stop that.
 *
 * An unknown email is fine — a person who does not exist yet holds no role and
 * belongs to no unit, and `addProjectMember` enrols them in the project's unit
 * as it mints them.
 */
export function projectMembershipBlock(
  projectId: string,
  emailOrIdentityId: string,
): string | null {
  const identity =
    getIdentity(emailOrIdentityId) ?? getIdentityByEmail(emailOrIdentityId) ?? null;
  if (!identity) return null;

  const memberships = listMembershipsForIdentity(identity.id);

  const governance = memberships.find(
    (m) => m.role in ROLE_META && ROLE_META[m.role as PlatformRole].governanceOnly,
  );
  if (governance) {
    return `${identity.displayName} is a ${ROLE_META[governance.role as PlatformRole].label}. Governance roles approve and fund projects, so they are never members of one.`;
  }

  const projectUnit = getProjectById(projectId)?.workspaceId;
  // A project whose unit cannot be resolved is not a reason to refuse someone —
  // that is a broken fixture, not a boundary being crossed.
  if (!projectUnit) return null;

  const units = memberships.map((m) => String(m.workspaceId));
  // Belongs nowhere yet: they are about to be enrolled in this project's unit.
  if (units.length === 0) return null;
  if (units.includes(String(projectUnit))) return null;

  const theirUnit = getWorkspace(units[0]!)?.displayName ?? "another business unit";
  const thisUnit = getWorkspace(String(projectUnit))?.displayName ?? "this project's";
  return `${identity.displayName} is in ${theirUnit}; this project belongs to ${thisUnit}. A project is staffed from its own ${BUSINESS_UNIT_LABEL.toLowerCase()}.`;
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
  input: { email: string; displayName?: string; roleName: string; extraAgents?: string[] },
): ProjectMember {
  const identity = findOrCreateIdentity(input.email, input.displayName);
  // findOrCreateIdentity stamps a "pending|" ssoSubject only on identities it
  // just minted — the reliable signal that this is a genuinely new person,
  // as opposed to an existing person joining this project for the first time.
  const isNewPerson = identity.ssoSubject.startsWith("pending|");

  /**
   * Enrol a person who belongs nowhere into this project's Business Unit.
   *
   * Without this, adding someone by email produced a project member with no
   * unit — outside every roster, every budget and every admin's reach, and
   * invisible in the people directory, which lists people by the unit they
   * belong to. `contributor` is the honest role: they are on a project, and
   * the unit's admin has not yet said what they do in the unit itself.
   */
  const projectUnit = getProjectById(projectId)?.workspaceId;
  if (projectUnit && listMembershipsForIdentity(identity.id).length === 0) {
    setMembershipRole(String(projectUnit), identity.id, "contributor");
  }

  const row: ProjectMembershipRow = {
    id: `pm_${nextId++}`,
    projectId,
    identityId: identity.id,
    role: input.roleName,
    status: isNewPerson ? "invited" : "active",
    addedAt: new Date().toISOString(),
    // Undefined rather than [] when none were granted — see the note on the
    // create input; the two must not become distinguishable states.
    extraAgents: input.extraAgents?.length ? [...input.extraAgents] : undefined,
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

/**
 * Drop every project seat this person holds inside one Business Unit.
 *
 * Called when the Organization Admin moves someone to a different unit. Their
 * seats do not travel: the projects belong to the unit they are leaving, are
 * funded by its budget and governed by its admin, and leaving the rows in place
 * would produce exactly the cross-unit membership `projectMembershipBlock`
 * refuses to create in the first place.
 *
 * Returns how many were removed, so the caller can say so rather than moving
 * someone off three projects silently.
 */
export function removeProjectMembershipsInWorkspace(
  identityId: string,
  workspaceId: string,
): number {
  const doomed = PROJECT_MEMBERSHIPS.filter(
    (r) =>
      r.identityId === String(identityId) &&
      String(getProjectById(r.projectId)?.workspaceId ?? "") === String(workspaceId),
  );
  for (const row of doomed) {
    PROJECT_MEMBERSHIPS.splice(PROJECT_MEMBERSHIPS.indexOf(row), 1);
  }
  return doomed.length;
}

export function removeProjectMember(projectId: string, membershipId: string): boolean {
  const i = PROJECT_MEMBERSHIPS.findIndex(
    (r) => r.projectId === projectId && r.id === membershipId,
  );
  if (i === -1) return false;
  PROJECT_MEMBERSHIPS.splice(i, 1);
  return true;
}
