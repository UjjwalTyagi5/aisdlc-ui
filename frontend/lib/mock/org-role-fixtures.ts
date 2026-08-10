/**
 * The org-level appointment — one record per person, written when the
 * Organization Admin onboards them.
 *
 * WHY THIS IS A SEPARATE STORE from the Business Unit memberships in
 * `workspace-fixtures.ts`. The two answer different questions, and the redesign
 * turns on keeping them apart:
 *
 *   org role      what the Organization Admin decided — does this person RUN a
 *                 unit, or work in one. Exactly two answers
 *                 (`lib/roles.ts::ORG_ASSIGNABLE_ROLES`), and it can exist with
 *                 no unit at all, which is the case a membership row cannot
 *                 represent: a `bu_admin` appointed before anyone decided which
 *                 unit they get has nothing to be a member OF.
 *   membership    what they do inside a given unit — assigned by that unit's
 *                 admin, and the only thing that carries permissions.
 *
 * Collapsing them would force the optional-unit case into a fake membership,
 * and a membership in no unit is precisely the kind of row every scope filter
 * in `access-scope.ts` has to special-case.
 *
 * DUMMY-DATA SEAM, same in-memory mutable-array pattern as its neighbours, and
 * server-safe so the Next route handlers and the MSW handlers can both reach it
 * ([[msw-dual-runtime-mutation-rule]]).
 */
import { listAllMemberships, listIdentities } from "./workspace-fixtures";

/** The org-level appointment. `org_admin` is not assignable through onboarding
 *  — it appears here only because the seeded founder holds it. */
export type OrgRole = "org_admin" | "bu_admin" | "contributor";

export interface OrgRoleRecord {
  identityId: string;
  role: OrgRole;
  /** The unit they were onboarded into. Null for an Organization Admin (whose
   *  authority is org-wide) and for a Business Unit Admin not yet given one. */
  businessUnitId: string | null;
  onboardedAt: string;
}

const ORG_ROLES: OrgRoleRecord[] = [];
let seeded = false;

/**
 * Back-fill the seeded roster the first time anyone reads this store.
 *
 * DERIVED rather than hand-written, because a second hand-written list of the
 * same eleven people is a list that drifts: adding an identity to
 * `workspace-fixtures.ts` and forgetting it here would produce a person who is
 * in a Business Unit and yet absent from the org directory. The rules read the
 * memberships that already exist:
 *
 *   holds `org_admin` anywhere  → Organization Admin, no single unit
 *   holds `bu_admin` anywhere   → Business Unit Admin of that unit
 *   anything else               → Contributor, in the first unit they belong to
 *
 * Lazy rather than at module load: this module imports the membership store, and
 * seeding at import time would depend on that module's array literal having been
 * evaluated first — true today, and a silent empty roster the day the import
 * graph changes shape.
 */
function ensureSeeded() {
  if (seeded) return;
  seeded = true;

  const memberships = listAllMemberships();
  for (const identity of listIdentities()) {
    const held = memberships.filter((m) => String(m.identityId) === String(identity.id));
    const asOrgAdmin = held.find((m) => m.role === "org_admin");
    const asBuAdmin = held.find((m) => m.role === "bu_admin");

    ORG_ROLES.push({
      identityId: String(identity.id),
      role: asOrgAdmin ? "org_admin" : asBuAdmin ? "bu_admin" : "contributor",
      businessUnitId: asOrgAdmin
        ? null
        : String(asBuAdmin?.workspaceId ?? held[0]?.workspaceId ?? "") || null,
      onboardedAt: "2026-02-01T09:00:00.000Z",
    });
  }
}

export function listOrgRoles(): OrgRoleRecord[] {
  ensureSeeded();
  return [...ORG_ROLES];
}

export function getOrgRole(identityId: string): OrgRoleRecord | undefined {
  ensureSeeded();
  return ORG_ROLES.find((r) => r.identityId === String(identityId));
}

/**
 * Record (or change) someone's org-level appointment.
 *
 * Upsert, not append: re-onboarding an email that already exists is a correction
 * — "actually, Priya runs Lending" — and appending would leave the person
 * holding two contradictory org roles with no rule for which one wins.
 */
export function setOrgRole(
  identityId: string,
  role: OrgRole,
  businessUnitId: string | null,
): OrgRoleRecord {
  ensureSeeded();
  const existing = ORG_ROLES.find((r) => r.identityId === String(identityId));
  if (existing) {
    existing.role = role;
    existing.businessUnitId = businessUnitId;
    return existing;
  }
  const created: OrgRoleRecord = {
    identityId: String(identityId),
    role,
    businessUnitId,
    onboardedAt: new Date().toISOString(),
  };
  ORG_ROLES.push(created);
  return created;
}
