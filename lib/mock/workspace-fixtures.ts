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
  // ── Delivery people ───────────────────────────────────────────────────────
  // Added when the governance tier was taken off every project
  // (project-membership-fixtures.ts): the seats Marcus, Farah and Noah held
  // were real delivery seats, so removing them without replacements would have
  // left two projects with no admin and the roster thinner than the product it
  // describes.
  {
    id: "idn_yuki",
    ssoSubject: "okta|yuki",
    email: "yuki@abcbank.com",
    displayName: "Yuki Tanaka",
    initials: "YT",
    idpSource: "Okta",
    links: [
      { id: "lnk_7", identityId: "idn_yuki", system: "jira", externalId: "5c22ff10a3d", handle: "yuki.tanaka", verified: true, provenance: "oauth", linkedAt: now },
    ],
  },
  {
    id: "idn_ravi",
    ssoSubject: "okta|ravi",
    email: "ravi@abcbank.com",
    displayName: "Ravi Sharma",
    initials: "RS",
    idpSource: "Okta",
    links: [
      { id: "lnk_8", identityId: "idn_ravi", system: "github", externalId: "MDQ6VXNlcjM", handle: "rsharma", verified: true, provenance: "oauth", linkedAt: now },
    ],
  },
  {
    id: "idn_sofia",
    ssoSubject: "okta|sofia",
    email: "sofia@abcbank.com",
    displayName: "Sofia Rossi",
    initials: "SR",
    idpSource: "Okta",
    links: [],
  },
  {
    id: "idn_omar",
    ssoSubject: "okta|omar",
    email: "omar@abcbank.com",
    displayName: "Omar Nasser",
    initials: "ON",
    idpSource: "Okta",
    links: [
      { id: "lnk_9", identityId: "idn_omar", system: "github", externalId: "", handle: "omar-n", verified: false, provenance: "admin", linkedAt: null },
    ],
  },
  {
    id: "idn_hana",
    ssoSubject: "okta|hana",
    email: "hana@abcbank.com",
    displayName: "Hana Kim",
    initials: "HK",
    idpSource: "Okta",
    links: [],
  },
  {
    id: "idn_luca",
    ssoSubject: "okta|luca",
    email: "luca@abcbank.com",
    displayName: "Luca Moretti",
    initials: "LM",
    idpSource: "Okta",
    links: [],
  },
  // Added when every project team was rebuilt inside its own unit. Diego and
  // Lena used to cover two units each, which is what let a Payments person show
  // up on a Lending project; each unit now staffs its own projects, and that
  // needs more people than a roster built around two who spanned everything.
  {
    id: "idn_iris",
    ssoSubject: "okta|iris",
    email: "iris@abcbank.com",
    displayName: "Iris Chen",
    initials: "IC",
    idpSource: "Okta",
    links: [
      { id: "lnk_10", identityId: "idn_iris", system: "github", externalId: "MDQ6VXNlcjQ", handle: "irischen", verified: true, provenance: "oauth", linkedAt: now },
    ],
  },
  {
    id: "idn_bruno",
    ssoSubject: "okta|bruno",
    email: "bruno@abcbank.com",
    displayName: "Bruno Alves",
    initials: "BA",
    idpSource: "Okta",
    links: [],
  },
  {
    id: "idn_ana",
    ssoSubject: "okta|ana",
    email: "ana@abcbank.com",
    displayName: "Ana Silva",
    initials: "AS",
    idpSource: "Okta",
    links: [
      { id: "lnk_11", identityId: "idn_ana", system: "jira", externalId: "6d31aa9c1f2", handle: "ana.silva", verified: true, provenance: "scim", linkedAt: now },
    ],
  },
  {
    id: "idn_rafael",
    ssoSubject: "okta|rafael",
    email: "rafael@abcbank.com",
    displayName: "Rafael Costa",
    initials: "RC",
    idpSource: "Okta",
    links: [],
  },
  {
    id: "idn_ingrid",
    ssoSubject: "okta|ingrid",
    email: "ingrid@abcbank.com",
    displayName: "Ingrid Larsen",
    initials: "IL",
    idpSource: "Okta",
    links: [],
  },
  {
    id: "idn_nadia",
    ssoSubject: "okta|nadia",
    email: "nadia@abcbank.com",
    displayName: "Nadia Petrov",
    initials: "NP",
    idpSource: "Okta",
    links: [],
  },
  {
    id: "idn_sam",
    ssoSubject: "okta|sam",
    email: "sam@abcbank.com",
    displayName: "Sam Okoye",
    initials: "SO",
    idpSource: "Okta",
    links: [],
  },
  // Contributors the Org Admin has onboarded into a unit and nobody has given a
  // job to yet — the `contributor` placeholder. Seeded, not merely reachable by
  // onboarding someone: a Business Unit Admin's pending-assignment queue that is
  // empty on every fresh process looks like a broken feature rather than an
  // empty one, and the queue is the whole point of the handover.
  {
    id: "idn_tomas",
    ssoSubject: "okta|tomas",
    email: "tomas@abcbank.com",
    displayName: "Tomas Bauer",
    initials: "TB",
    idpSource: "Okta",
    links: [],
  },
  {
    id: "idn_amara",
    ssoSubject: "okta|amara",
    email: "amara@abcbank.com",
    displayName: "Amara Okafor",
    initials: "AO",
    idpSource: "Okta",
    links: [],
  },
  {
    id: "idn_elif",
    ssoSubject: "okta|elif",
    email: "elif@abcbank.com",
    displayName: "Elif Demir",
    initials: "ED",
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
  { id: "ws_payments", organizationId: ORG.id, slug: "payments", displayName: "Payments", businessUnit: "Payments", costCenter: "CC-4100", status: "active", isActive: true, memberCount: 4, projectCount: 2, monthlySpendUsd: 12284.55, monthlyBudgetUsd: 12800, budgetStartDate: "2026-04-01", budgetEndDate: "2027-03-31", createdAt: "2026-02-01T09:00:00.000Z" },
  { id: "ws_lending", organizationId: ORG.id, slug: "lending", displayName: "Lending", businessUnit: "Lending", costCenter: "CC-4200", status: "active", isActive: true, memberCount: 3, projectCount: 2, monthlySpendUsd: 6420.10, monthlyBudgetUsd: 11000, budgetStartDate: "2026-01-01", budgetEndDate: null, createdAt: "2026-02-12T09:00:00.000Z" },
  { id: "ws_platform", organizationId: ORG.id, slug: "platform", displayName: "Platform Engineering", businessUnit: "Shared Services", costCenter: "CC-1000", status: "active", isActive: false, memberCount: 2, projectCount: 1, monthlySpendUsd: 3184.40, monthlyBudgetUsd: 9000, createdAt: "2026-01-20T09:00:00.000Z" },
] as Workspace[]);

// ───────── Memberships (identity × Business Unit × role) ─────────
// Roles are drawn strictly from the platform's twelve (PRD §33.1 plus Scrum Master) — there is
// no "Product Manager", "QA Lead" or "Release Manager" in the role catalogue.
//
// ONE BUSINESS UNIT PER PERSON, and the Organization Admin is the only
// exception. A person belongs to the unit that onboarded them, and every
// project they can be put on lives inside it (`projectMembershipBlock`); the
// unit is what makes them reachable, budgeted and governed at all.
//
// Diego used to be `developer` in Payments AND `architect` in Lending, and Lena
// sat in Lending AND Platform Engineering. That made a role a binding, which is
// true — but it also let a Payments person appear on a Lending project's roster
// and in Lending's people list, which is not. The demonstration survives inside
// one unit: Diego is Developer on one Payments project and Architect on
// another, so a role is still a binding of (person, scope, role).
//
// The Org Admin holds a row in every unit because their authority is org-wide
// rather than a membership — they are never on a project either way.
//
// Tier separation is per-scope (PRD §14.6, `lib/roles.ts::scopeTierConflicts`):
// no one holds a governance AND a delivery role in the SAME unit. The three
// `bu_admin` rows below hold NOTHING else anywhere — a governance role is never
// a project member, in their own unit or any other (see the note at the top of
// project-membership-fixtures.ts).
const MEMBERSHIPS: Membership[] = ([
  { id: "mb_1", identityId: "idn_sarthak", workspaceId: "ws_payments", role: "org_admin", status: "active" },
  { id: "mb_5", identityId: "idn_sarthak", workspaceId: "ws_lending", role: "org_admin", status: "active" },
  { id: "mb_8", identityId: "idn_sarthak", workspaceId: "ws_platform", role: "org_admin", status: "active" },

  // ── Payments ──────────────────────────────────────────────────────────────
  { id: "mb_10", identityId: "idn_marcus", workspaceId: "ws_payments", role: "bu_admin", status: "active" },
  { id: "mb_2", identityId: "idn_priya", workspaceId: "ws_payments", role: "project_admin", status: "active" },
  { id: "mb_3", identityId: "idn_diego", workspaceId: "ws_payments", role: "developer", status: "active" },
  { id: "mb_4", identityId: "idn_wei", workspaceId: "ws_payments", role: "qa", status: "invited", invitedAt: now },
  { id: "mb_19", identityId: "idn_hana", workspaceId: "ws_payments", role: "security_engineer", status: "active" },
  { id: "mb_22", identityId: "idn_iris", workspaceId: "ws_payments", role: "architect", status: "active" },
  { id: "mb_23", identityId: "idn_bruno", workspaceId: "ws_payments", role: "data_engineer", status: "active" },

  // ── Lending ───────────────────────────────────────────────────────────────
  { id: "mb_11", identityId: "idn_farah", workspaceId: "ws_lending", role: "bu_admin", status: "active" },
  { id: "mb_15", identityId: "idn_yuki", workspaceId: "ws_lending", role: "project_admin", status: "active" },
  { id: "mb_24", identityId: "idn_ana", workspaceId: "ws_lending", role: "project_admin", status: "active" },
  { id: "mb_16", identityId: "idn_sofia", workspaceId: "ws_lending", role: "ba", status: "active" },
  { id: "mb_25", identityId: "idn_rafael", workspaceId: "ws_lending", role: "architect", status: "active" },
  { id: "mb_7", identityId: "idn_lena", workspaceId: "ws_lending", role: "devops_engineer", status: "active" },
  { id: "mb_26", identityId: "idn_ingrid", workspaceId: "ws_lending", role: "qa", status: "active" },

  // ── Platform Engineering ──────────────────────────────────────────────────
  { id: "mb_12", identityId: "idn_noah", workspaceId: "ws_platform", role: "bu_admin", status: "active" },
  { id: "mb_17", identityId: "idn_ravi", workspaceId: "ws_platform", role: "project_admin", status: "active" },
  { id: "mb_18", identityId: "idn_omar", workspaceId: "ws_platform", role: "developer", status: "active" },
  { id: "mb_27", identityId: "idn_nadia", workspaceId: "ws_platform", role: "devops_engineer", status: "active" },
  { id: "mb_28", identityId: "idn_sam", workspaceId: "ws_platform", role: "qa", status: "active" },
  { id: "mb_20", identityId: "idn_luca", workspaceId: "ws_payments", role: "scrum_master", status: "active" },

  // Awaiting a Business Unit role — ONE PER UNIT, so the queue is non-empty
  // whichever admin signs in and is visibly per-unit rather than global. Each
  // has a matching `role_assignment` request seeded in
  // governance-approval-fixtures.ts; the two are one obligation and drift apart
  // if only one is seeded.
  { id: "mb_13", identityId: "idn_tomas", workspaceId: "ws_platform", role: "contributor", status: "invited", invitedAt: now },
  { id: "mb_14", identityId: "idn_amara", workspaceId: "ws_payments", role: "contributor", status: "invited", invitedAt: now },
  { id: "mb_21", identityId: "idn_elif", workspaceId: "ws_lending", role: "contributor", status: "invited", invitedAt: now },
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

/**
 * Decorate a unit with the two fields that are answers ABOUT the membership
 * store rather than facts stored on the unit.
 *
 * `memberCount` is derived rather than maintained. It used to be a hand-written
 * seed number that three mutators kept nudging, and the seeds had already
 * drifted from the rows: Payments said four members while six memberships named
 * it. A count that disagrees with the list it counts is worse than no count,
 * and every way of keeping the two in step by hand is a way of forgetting to.
 */
function withBuAdmin(w: Workspace): Workspace {
  return {
    ...w,
    buAdminName: buAdminNameFor(w.id),
    memberCount: MEMBERSHIPS.filter((m) => m.workspaceId === w.id).length,
  };
}

/**
 * Who to tell when something happens to a Business Unit's membership — the
 * identity ids of its active admins.
 *
 * A LIST, though `buAdminNameFor` above resolves one. Reading a single admin is
 * right for a label (a unit has one holder to name); addressing a notification
 * to only the first would silently drop the message during the window where a
 * unit has two rows, which is exactly when a handover is in progress and the
 * message matters most.
 */
export function buAdminIdentityIdsFor(workspaceId: string): string[] {
  return MEMBERSHIPS.filter(
    (m) => m.workspaceId === workspaceId && m.role === "bu_admin" && m.status === "active",
  ).map((m) => String(m.identityId));
}

/** Every membership row, for the org-wide people directory. Callers filter;
 *  this returns the whole store deliberately (the directory is org-wide by
 *  design — see app/api/admin/directory/route.ts). */
export function listAllMemberships(): Membership[] {
  return [...MEMBERSHIPS];
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

  // A newcomer needs the membership row created; someone already in the unit
  // does not.
  const already = MEMBERSHIPS.some(
    (m) => m.workspaceId === workspaceId && m.identityId === identity.id,
  );
  if (!already) createMembership(workspaceId, identity.id, "bu_admin");

  // Then promote unconditionally, because `createMembership` writes
  // status:"invited" — right for an invitation the person has yet to accept,
  // wrong for this. An Org Admin appointing an admin is a decision, not a
  // request, and `buAdminNameFor` above only resolves ACTIVE bu_admins: leaving
  // the row invited demotes the outgoing holder and leaves the unit reading
  // "No admin appointed", which is the exact broken state this function exists
  // to prevent. setMembershipRole sets both the role and status:"active".
  setMembershipRole(workspaceId, identity.id, "bu_admin");

  return { admin: identity, replaced };
}

/** Remove an identity's membership from a workspace — the Members page's
 *  "Remove" action. */
export function removeMembership(workspaceId: string, identityId: string): boolean {
  const idx = MEMBERSHIPS.findIndex((m) => m.workspaceId === workspaceId && m.identityId === identityId);
  if (idx === -1) return false;
  MEMBERSHIPS.splice(idx, 1);
  return true;
}
