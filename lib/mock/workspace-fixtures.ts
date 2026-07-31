/**
 * Frontend-first mock data + in-memory mutators for the workspaces/identity
 * feature. Imported by the Next route handlers (app/api/workspaces/*) AND by the
 * MSW handlers — plain data + pure functions, safe on server and browser.
 *
 * THIS IS THE BACKEND-REPLACEMENT SEAM: when FastAPI grows real workspace/identity
 * endpoints, the route handlers proxy to it and this module is deleted. The shapes
 * here are the contract (see lib/schemas/workspace.ts).
 */
import type {
  Identity,
  Membership,
  Organization,
  Workspace,
  WorkspaceMember,
} from "@/lib/schemas/workspace";

const now = "2026-06-15T10:00:00.000Z";

export const ORG: Organization = {
  id: "org_acme",
  slug: "acme-bank",
  displayName: "ABC Bank",
  status: "active",
  region: "us",
  plan: "enterprise",
  createdAt: "2026-01-04T09:00:00.000Z",
} as Organization;

// ───────── Identities (one per human; SSO is source of truth) ─────────
// Note the mix of verified/unverified links and an unlinked system — this is
// exactly the "how is this person tracked across systems" surface (F3).
const IDENTITIES: Identity[] = [
  {
    id: "idn_sarthak",
    ssoSubject: "okta|sarthak",
    email: "srk02804@gmail.com",
    displayName: "Sarthak Kapoor",
    initials: "SK",
    idpSource: "Okta",
    links: [
      { id: "lnk_1", identityId: "idn_sarthak", system: "github", externalId: "MDQ6VXNlcjE", handle: "srkapoor", verified: true, provenance: "oauth", linkedAt: now },
      { id: "lnk_2", identityId: "idn_sarthak", system: "azure_devops", externalId: "aad.7f3c", handle: "sarthak@abcbank.com", verified: true, provenance: "scim", linkedAt: now },
    ],
  },
  {
    id: "idn_priya",
    ssoSubject: "okta|priya",
    email: "priya@abcbank.com",
    displayName: "Priya Menon",
    initials: "PM",
    idpSource: "Okta",
    links: [
      { id: "lnk_3", identityId: "idn_priya", system: "jira", externalId: "5b10ac8d82e", handle: "priya", verified: true, provenance: "oauth", linkedAt: now },
      { id: "lnk_4", identityId: "idn_priya", system: "github", externalId: "", handle: "priya-personal", verified: false, provenance: "admin", linkedAt: null },
    ],
  },
  {
    id: "idn_diego",
    ssoSubject: "okta|diego",
    email: "diego@abcbank.com",
    displayName: "Diego Alvarez",
    initials: "DA",
    idpSource: "Okta",
    links: [
      { id: "lnk_5", identityId: "idn_diego", system: "github", externalId: "MDQ6VXNlcjI", handle: "dalvarez", verified: true, provenance: "oauth", linkedAt: now },
    ],
  },
  {
    id: "idn_wei",
    ssoSubject: "okta|wei",
    email: "wei@abcbank.com",
    displayName: "Wei Chen",
    initials: "WC",
    idpSource: "Okta",
    links: [],
  },
  {
    id: "idn_lena",
    ssoSubject: "okta|lena",
    email: "lena@abcbank.com",
    displayName: "Lena Fischer",
    initials: "LF",
    idpSource: "Okta",
    links: [
      { id: "lnk_6", identityId: "idn_lena", system: "azure_devops", externalId: "aad.91ab", handle: "lena@abcbank.com", verified: true, provenance: "scim", linkedAt: now },
    ],
  },
  // Business Unit Admins — one per seeded workspace (see MEMBERSHIPS' bu_admin
  // rows below). Distinct from the delivery-tier identities above.
  {
    id: "idn_marcus",
    ssoSubject: "okta|marcus",
    email: "marcus@abcbank.com",
    displayName: "Marcus Reyes",
    initials: "MR",
    idpSource: "Okta",
    links: [],
  },
  {
    id: "idn_farah",
    ssoSubject: "okta|farah",
    email: "farah@abcbank.com",
    displayName: "Farah Haddad",
    initials: "FH",
    idpSource: "Okta",
    links: [],
  },
  {
    id: "idn_noah",
    ssoSubject: "okta|noah",
    email: "noah@abcbank.com",
    displayName: "Noah Bennett",
    initials: "NB",
    idpSource: "Okta",
    links: [],
  },
] as Identity[];

// ───────── Workspaces (Business Units — PRD §12) ─────────
// Each unit isolates its own budget, connections and data. Budgets nest: a unit
// cap can never exceed the organization cap (PRD §34.5).
// Payments is deliberately at 96% of cap so the Dashboard attention list has a
// real "budget near cap" item to surface (PRD §36).
//
// `isActive` is the Org Admin's active/inactive marker, orthogonal to `status`
// (archive lifecycle) — see lib/schemas/workspace.ts. Platform Engineering is
// seeded inactive on purpose: with every unit active the "Inactive" badge never
// renders and a working feature reads as a missing one.
const WORKSPACES: Workspace[] = ([
  // projectCount mirrors the active (non-archived) PROJECTS in mocks/fixtures.ts
  // tagged with this workspaceId — keep the two in sync if projects are added,
  // removed, or reassigned to a different Business Unit.
  // Payments carries a closed budget window (the funded FY26 period) and
  // Lending an open-ended one, so both shapes render somewhere.
  { id: "ws_payments", organizationId: ORG.id, slug: "payments", displayName: "Payments", businessUnit: "Payments", costCenter: "CC-4100", dataClassification: "restricted", status: "active", isActive: true, memberCount: 4, projectCount: 2, monthlySpendUsd: 12284.55, monthlyBudgetUsd: 12800, budgetStartDate: "2026-04-01", budgetEndDate: "2027-03-31", createdAt: "2026-02-01T09:00:00.000Z" },
  { id: "ws_lending", organizationId: ORG.id, slug: "lending", displayName: "Lending", businessUnit: "Lending", costCenter: "CC-4200", dataClassification: "confidential", status: "active", isActive: true, memberCount: 3, projectCount: 2, monthlySpendUsd: 6420.10, monthlyBudgetUsd: 11000, budgetStartDate: "2026-01-01", budgetEndDate: null, createdAt: "2026-02-12T09:00:00.000Z" },
  { id: "ws_platform", organizationId: ORG.id, slug: "platform", displayName: "Platform Engineering", businessUnit: "Shared Services", costCenter: "CC-1000", dataClassification: "internal", status: "active", isActive: false, memberCount: 2, projectCount: 1, monthlySpendUsd: 3184.40, monthlyBudgetUsd: 9000, createdAt: "2026-01-20T09:00:00.000Z" },
] as Workspace[]);

// ───────── Memberships (identity × Business Unit × role) ─────────
// Roles are drawn strictly from the platform's twelve (PRD §33.1 plus Scrum Master) — there is
// no "Product Manager", "QA Lead" or "Release Manager" in the role catalogue.
//
// Diego is `developer` in Payments AND `architect` in Lending — a role is a
// binding of (user, scope, role), not a property of the person (PRD §33.1).
//
// Tier separation is per-scope (PRD §14.6, `lib/roles.ts::scopeTierConflicts`):
// no one holds a governance AND a delivery role in the SAME unit, but Marcus
// and Farah are each `bu_admin` here and delivery contributors on projects in
// *other* units — see the seeded roster in project-membership-fixtures.ts.
const MEMBERSHIPS: Membership[] = ([
  { id: "mb_1", identityId: "idn_sarthak", workspaceId: "ws_payments", role: "org_admin", status: "active" },
  { id: "mb_2", identityId: "idn_priya", workspaceId: "ws_payments", role: "ba", status: "active" },
  { id: "mb_3", identityId: "idn_diego", workspaceId: "ws_payments", role: "developer", status: "active" },
  { id: "mb_4", identityId: "idn_wei", workspaceId: "ws_payments", role: "qa", status: "invited", invitedAt: now },
  { id: "mb_5", identityId: "idn_sarthak", workspaceId: "ws_lending", role: "org_admin", status: "active" },
  { id: "mb_6", identityId: "idn_diego", workspaceId: "ws_lending", role: "architect", status: "active" },
  { id: "mb_7", identityId: "idn_lena", workspaceId: "ws_lending", role: "devops_engineer", status: "active" },
  { id: "mb_8", identityId: "idn_sarthak", workspaceId: "ws_platform", role: "org_admin", status: "active" },
  { id: "mb_9", identityId: "idn_lena", workspaceId: "ws_platform", role: "devops_engineer", status: "active" },
  { id: "mb_10", identityId: "idn_marcus", workspaceId: "ws_payments", role: "bu_admin", status: "active" },
  { id: "mb_11", identityId: "idn_farah", workspaceId: "ws_lending", role: "bu_admin", status: "active" },
  { id: "mb_12", identityId: "idn_noah", workspaceId: "ws_platform", role: "bu_admin", status: "active" },
] as Membership[]);

function slugify(s: string): string {
  return s.toLowerCase().trim().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
}

/** The active `bu_admin` membership's display name for a workspace, or null
 *  if none has been appointed yet. */
function buAdminNameFor(workspaceId: string): string | null {
  const membership = MEMBERSHIPS.find(
    (m) => m.workspaceId === workspaceId && m.role === "bu_admin" && m.status === "active",
  );
  if (!membership) return null;
  return IDENTITIES.find((i) => i.id === membership.identityId)?.displayName ?? null;
}

function withBuAdmin(w: Workspace): Workspace {
  return { ...w, buAdminName: buAdminNameFor(w.id) };
}

// ───────── Pure accessors / mutators (in-memory; resets on server restart) ─────────

/**
 * Every human in the organization — one record per person, not per membership.
 *
 * The Users page composes its directory client-side by fanning out over unit
 * and project member lists, which is fine for a table but double-counts anyone
 * in more than one unit (Sarthak is in all three). A headline "total users"
 * figure has to be distinct identities, so it reads from here instead.
 */
export function listIdentities(): Identity[] {
  return [...IDENTITIES];
}

export function listWorkspaces(): Workspace[] {
  return [...WORKSPACES].sort((a, b) => a.displayName.localeCompare(b.displayName)).map(withBuAdmin);
}

export function getWorkspace(id: string): Workspace | undefined {
  const w = WORKSPACES.find((w) => w.id === id);
  return w ? withBuAdmin(w) : undefined;
}

export function createWorkspace(input: {
  displayName: string;
  businessUnit?: string;
  costCenter?: string;
  dataClassification?: Workspace["dataClassification"];
  /** Null / omitted = no cap set; the unit's own Admin can set one later. */
  monthlyBudgetUsd?: number | null;
  budgetStartDate?: string | null;
  budgetEndDate?: string | null;
  isActive?: boolean;
}): Workspace {
  const created = {
    id: `ws_${slugify(input.displayName) || Math.random().toString(36).slice(2, 8)}`,
    organizationId: ORG.id,
    slug: slugify(input.displayName),
    displayName: input.displayName,
    businessUnit: input.businessUnit ?? null,
    costCenter: input.costCenter ?? null,
    dataClassification: input.dataClassification ?? "internal",
    status: "active",
    isActive: input.isActive ?? true,
    memberCount: 0,
    projectCount: 0,
    monthlySpendUsd: 0,
    monthlyBudgetUsd: input.monthlyBudgetUsd ?? null,
    budgetStartDate: input.budgetStartDate ?? null,
    budgetEndDate: input.budgetEndDate ?? null,
    createdAt: new Date().toISOString(),
  } as Workspace;
  WORKSPACES.unshift(created);
  return created;
}

export function patchWorkspace(
  id: string,
  patch: Partial<
    Pick<
      Workspace,
      | "displayName"
      | "businessUnit"
      | "costCenter"
      | "dataClassification"
      | "status"
      | "isActive"
      | "monthlyBudgetUsd"
      | "budgetStartDate"
      | "budgetEndDate"
    >
  >,
): Workspace | undefined {
  const w = WORKSPACES.find((x) => x.id === id);
  if (!w) return undefined;
  Object.assign(w, patch);
  return w;
}

export function archiveWorkspace(id: string): Workspace | undefined {
  return patchWorkspace(id, { status: "archived" });
}

export function listMembers(workspaceId: string): WorkspaceMember[] {
  return MEMBERSHIPS.filter((m) => m.workspaceId === workspaceId)
    .map((m) => {
      const identity = IDENTITIES.find((i) => i.id === m.identityId);
      if (!identity) return null;
      return { membershipId: m.id, identity, role: m.role, status: m.status };
    })
    .filter((x): x is WorkspaceMember => x !== null);
}

export function getIdentity(id: string): Identity | undefined {
  return IDENTITIES.find((i) => i.id === id);
}

export function getIdentityBySsoSubject(ssoSubject: string): Identity | undefined {
  return IDENTITIES.find((i) => i.ssoSubject === ssoSubject);
}

/** Resolve a person by email — the fallback path for sessions that carry no
 *  identity id (local email+password auth). Case-insensitive; never mints. */
export function getIdentityByEmail(email: string): Identity | undefined {
  const needle = email.trim().toLowerCase();
  if (!needle) return undefined;
  return IDENTITIES.find((i) => i.email?.toLowerCase() === needle);
}

/** Every (workspace, role) binding an identity holds, across all Business
 *  Units — the cross-scope join the Users page's detail view needs. */
export function listMembershipsForIdentity(identityId: string): Membership[] {
  return MEMBERSHIPS.filter((m) => m.identityId === identityId);
}

/**
 * Resolve a person by email, or mint a brand-new `Identity` for one that
 * doesn't exist yet — the onboarding primitive every "invite a new person"
 * entry point (org/BU/project scope) funnels through, so a not-yet-known
 * email always resolves rather than 404ing.
 */
export function findOrCreateIdentity(email: string, displayName?: string): Identity {
  const existing = IDENTITIES.find((i) => i.email?.toLowerCase() === email.toLowerCase());
  if (existing) return existing;

  const name = displayName?.trim() || email.split("@", 1)[0] || email;
  const parts = name.trim().split(/\s+/);
  const initials = ((parts[0]?.[0] ?? "") + (parts[1]?.[0] ?? "")).toUpperCase() || "?";
  const created: Identity = {
    id: `idn_${slugify(email.split("@", 1)[0] || email)}_${IDENTITIES.length + 1}` as Identity["id"],
    ssoSubject: `pending|${email}`,
    email,
    displayName: name,
    initials,
    idpSource: null,
    links: [],
  };
  IDENTITIES.push(created);
  return created;
}

/**
 * Create an "invited" membership for an identity in a workspace — the first
 * code path that can actually produce `status: "invited"` (previously only
 * present as static, unreachable fixture data).
 */
export function createMembership(workspaceId: string, identityId: string, role: string): Membership {
  const created: Membership = {
    id: `mb_${MEMBERSHIPS.length + 1}_${Date.now()}` as Membership["id"],
    identityId: identityId as Membership["identityId"],
    workspaceId: workspaceId as Membership["workspaceId"],
    role,
    status: "invited",
    invitedAt: now,
  };
  MEMBERSHIPS.push(created);
  const workspace = WORKSPACES.find((w) => w.id === workspaceId);
  if (workspace) workspace.memberCount += 1;
  return created;
}

/**
 * Resolve a person by SSO subject (the id `listOrgMembers()`/the workspace
 * Members picker uses), or mint one on the fly — mirrors `findOrCreateIdentity`
 * but keyed by ssoSubject instead of email, since the org-wide member roster
 * (lib/mock/access-fixtures.ts::ORG_MEMBERS) is a separate seed pool that
 * doesn't share ids with this file's IDENTITIES.
 */
export function findOrCreateIdentityBySsoSubject(
  ssoSubject: string,
  email?: string | null,
  initials?: string,
): Identity {
  const existing = IDENTITIES.find((i) => i.ssoSubject === ssoSubject);
  if (existing) return existing;

  const name = email?.split("@", 1)[0] || ssoSubject;
  const created: Identity = {
    id: `idn_${slugify(name)}_${IDENTITIES.length + 1}` as Identity["id"],
    ssoSubject,
    email: email ?? null,
    displayName: name,
    initials: initials?.toUpperCase() || "?",
    idpSource: null,
    links: [],
  };
  IDENTITIES.push(created);
  return created;
}

/**
 * Set (create or change) an identity's role in a workspace — the workspace
 * Members page's "Add"/"Change role" actions. Unlike `createMembership`
 * (always "invited"), an explicit role assignment here is immediately
 * "active" — there's no separate accept step in this flow.
 */
export function setMembershipRole(workspaceId: string, identityId: string, role: string): Membership {
  const existing = MEMBERSHIPS.find((m) => m.workspaceId === workspaceId && m.identityId === identityId);
  if (existing) {
    existing.role = role;
    existing.status = "active";
    return existing;
  }
  const created: Membership = {
    id: `mb_${MEMBERSHIPS.length + 1}_${Date.now()}` as Membership["id"],
    identityId: identityId as Membership["identityId"],
    workspaceId: workspaceId as Membership["workspaceId"],
    role,
    status: "active",
  };
  MEMBERSHIPS.push(created);
  const workspace = WORKSPACES.find((w) => w.id === workspaceId);
  if (workspace) workspace.memberCount += 1;
  return created;
}

/**
 * Replace who runs a Business Unit (PRD §15.2 — the Org Admin appoints and
 * re-appoints unit admins).
 *
 * A unit has exactly one admin: `buAdminNameFor` above resolves the first
 * active `bu_admin`, and the create dialog appoints precisely one, so leaving
 * the outgoing holder in place would produce a unit with two admins where
 * every read path shows only one — the worse failure, because it looks like
 * the change didn't take.
 *
 * The outgoing admin is demoted, not removed: they keep their membership and
 * their history in the unit, they just no longer run it. `previousRole` is
 * what they are dropped to, defaulting to a delivery role so the account stays
 * usable; the Org Admin can remove them outright from the Members list if
 * that's what they actually meant.
 */
export function setBusinessUnitAdmin(
  workspaceId: string,
  incoming: { email: string; displayName?: string },
  previousRole = "developer",
): { admin: Identity; replaced: Identity | null } | undefined {
  const workspace = WORKSPACES.find((w) => w.id === workspaceId);
  if (!workspace) return undefined;

  const identity = findOrCreateIdentity(incoming.email, incoming.displayName);

  const outgoing = MEMBERSHIPS.find(
    (m) =>
      m.workspaceId === workspaceId &&
      m.role === "bu_admin" &&
      m.status === "active" &&
      m.identityId !== identity.id,
  );
  const replaced = outgoing ? (IDENTITIES.find((i) => i.id === outgoing.identityId) ?? null) : null;
  if (outgoing) outgoing.role = previousRole;

  // createMembership for a newcomer (it maintains `memberCount`),
  // setMembershipRole for someone already in the unit (it must not).
  const already = MEMBERSHIPS.some(
    (m) => m.workspaceId === workspaceId && m.identityId === identity.id,
  );
  if (already) setMembershipRole(workspaceId, identity.id, "bu_admin");
  else createMembership(workspaceId, identity.id, "bu_admin");

  return { admin: identity, replaced };
}

/** Remove an identity's membership from a workspace — the Members page's
 *  "Remove" action. */
export function removeMembership(workspaceId: string, identityId: string): boolean {
  const idx = MEMBERSHIPS.findIndex((m) => m.workspaceId === workspaceId && m.identityId === identityId);
  if (idx === -1) return false;
  MEMBERSHIPS.splice(idx, 1);
  const workspace = WORKSPACES.find((w) => w.id === workspaceId);
  if (workspace) workspace.memberCount = Math.max(0, workspace.memberCount - 1);
  return true;
}
