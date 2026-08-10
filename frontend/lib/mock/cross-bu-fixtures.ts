/**
 * Cross-Business-Unit project access — the record that a contributor from one
 * unit was lent to a project in another, and by whom.
 *
 * WHY A GRANT AND NOT JUST A SEAT. The project seat already exists as a
 * `ProjectMembershipRow`; a grant would be redundant if the only question were
 * "are they on this project". It is not. The rules are:
 *
 *   - Their parent Business Unit NEVER changes. Ownership, the admin who
 *     manages them, and the unit they are counted against all stay put.
 *   - The lending is authorised by that PARENT unit's admin, not by the unit
 *     that wants them. Without a durable record, "was this approved" is only
 *     answerable from an audit trail nobody reads at write time.
 *   - Their reach in the borrowing unit is exactly one project. Not the unit,
 *     not its other projects, not its budget or connectors.
 *
 * So the grant is what `projectMembershipBlock` consults to let a seat exist at
 * all, and what a revoke removes to end the loan. A seat without a grant is the
 * thing the whole feature exists to prevent.
 *
 * DUMMY-DATA SEAM, same in-memory pattern as its neighbours and server-safe so
 * both runtimes reach it ([[msw-dual-runtime-mutation-rule]]).
 */

export interface CrossBuGrant {
  id: string;
  /** The lent contributor. */
  identityId: string;
  /** The one project they may reach in the borrowing unit. */
  projectId: string;
  /** The unit that owns them, and whose admin approved the loan. */
  parentWorkspaceId: string;
  /** The unit the project belongs to. */
  targetWorkspaceId: string;
  /** The project role approved — never `org_admin` or `bu_admin`. */
  role: string;
  approvedBy: string;
  approvedAt: string;
  /** The request this came from, so the grant and its decision stay joined. */
  requestId: string | null;
  revokedAt: string | null;
}

let nextId = 1;
const GRANTS: CrossBuGrant[] = [];

export function listCrossBuGrants(): CrossBuGrant[] {
  return GRANTS.filter((g) => g.revokedAt === null);
}

/** Every live loan into a unit — what a borrowing unit's admin has on loan. */
export function listCrossBuGrantsForWorkspace(targetWorkspaceId: string): CrossBuGrant[] {
  return listCrossBuGrants().filter(
    (g) => g.targetWorkspaceId === String(targetWorkspaceId),
  );
}

/** Every live loan OUT of a unit — what a parent unit's admin has lent. */
export function listCrossBuGrantsFromWorkspace(parentWorkspaceId: string): CrossBuGrant[] {
  return listCrossBuGrants().filter(
    (g) => g.parentWorkspaceId === String(parentWorkspaceId),
  );
}

/**
 * Is this person authorised on this project despite belonging elsewhere?
 *
 * Keyed on (person, project) and not on (person, unit): one approval buys one
 * project. Being lent to Lending's ledger project says nothing about Lending's
 * other work, which is the containment the requirement turns on.
 */
export function hasCrossBuGrant(identityId: string, projectId: string): boolean {
  return listCrossBuGrants().some(
    (g) => g.identityId === String(identityId) && g.projectId === String(projectId),
  );
}

export function grantCrossBuAccess(input: {
  identityId: string;
  projectId: string;
  parentWorkspaceId: string;
  targetWorkspaceId: string;
  role: string;
  approvedBy: string;
  requestId?: string | null;
}): CrossBuGrant {
  const existing = listCrossBuGrants().find(
    (g) =>
      g.identityId === String(input.identityId) && g.projectId === String(input.projectId),
  );
  // Re-approving the same person on the same project is a role change, not a
  // second loan — two live grants for one seat would make "what were they
  // approved for" ambiguous at exactly the moment it matters.
  if (existing) {
    existing.role = input.role;
    existing.approvedBy = input.approvedBy;
    existing.approvedAt = new Date().toISOString();
    existing.requestId = input.requestId ?? existing.requestId;
    return existing;
  }

  const created: CrossBuGrant = {
    id: `xbu_${nextId++}`,
    identityId: String(input.identityId),
    projectId: String(input.projectId),
    parentWorkspaceId: String(input.parentWorkspaceId),
    targetWorkspaceId: String(input.targetWorkspaceId),
    role: input.role,
    approvedBy: input.approvedBy,
    approvedAt: new Date().toISOString(),
    requestId: input.requestId ?? null,
    revokedAt: null,
  };
  GRANTS.push(created);
  return created;
}

/**
 * End the loan.
 *
 * Marked revoked rather than deleted: the grant is the answer to "who let this
 * person into our project, and when", and that question outlives the access.
 */
export function revokeCrossBuGrant(identityId: string, projectId: string): CrossBuGrant | null {
  const grant = listCrossBuGrants().find(
    (g) => g.identityId === String(identityId) && g.projectId === String(projectId),
  );
  if (!grant) return null;
  grant.revokedAt = new Date().toISOString();
  return grant;
}
