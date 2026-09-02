import type { Phase } from "@/lib/schemas/enums";

/**
 * The platform's twelve roles — PRD §33.1, §14.1, §15.2–§15.12, plus
 * Scrum Master (a platform addition beyond the original PRD's eleven,
 * added to give delivery teams a cross-cutting coordinator who observes
 * every stage but owns no single agent's gate). There is deliberately no
 * "Project Owner", "Compliance Officer" or "Executive Stakeholder": a client
 * persona needing a read-only rollup is served by an existing role's scoped
 * Dashboard (PRD §15.2 gives the Organization Admin org-wide visibility),
 * never by inventing a role for it.
 *
 * NOTE — this is the *product* role model from the PRD. It is a separate
 * concern from `lib/auth/types.ts::Role` ("admin" | "member" | "viewer"),
 * which is the coarse MVP-0 session role the backend currently issues. The
 * two coexist: this module drives what the UI *presents* (ownership, routing
 * of gates, page scoping); `hasPermission()` remains the enforcement mirror.
 */

export type PlatformRole =
  | "org_admin"
  | "bu_admin"
  | "contributor"
  | "project_admin"
  | "ba"
  | "architect"
  | "developer"
  | "qa"
  | "security_engineer"
  | "devops_engineer"
  | "data_engineer"
  | "scrum_master"
  | "custom";

/**
 * The two tiers (PRD §14.6). Separation of duties is a **per-scope** rule, not
 * a property of the person: within any single scope — one Business Unit, one
 * project — a person is either governing or delivering, never both, so nobody
 * ever approves their own work.
 *
 * Across *different* scopes the tiers may freely coexist. The same human can
 * be Business Unit Admin of Payments, Project Admin on a Lending project, and
 * BA on a third project: three separate bindings, three separate scopes, no
 * self-approval anywhere. See `scopeTierConflict()`.
 */
export type RoleTier = "governance" | "delivery";

/** The scope a role is bound at (PRD §12). */
export type RoleScope = "organization" | "business_unit" | "project" | "configurable";

export interface RoleMeta {
  label: string;
  /** Compact label for chips and dense tables. */
  shortLabel: string;
  scope: RoleScope;
  tier: RoleTier;
  /** One-line job, verbatim in spirit from PRD §33.1. */
  oneLiner: string;
  /** Governance-only roles never build and never approve delivery work. */
  governanceOnly: boolean;
  /** PRD section specifying this role's page reference. */
  prdSection: string;
}

export const ROLE_ORDER: readonly PlatformRole[] = [
  "org_admin",
  "bu_admin",
  "contributor",
  "project_admin",
  "ba",
  "architect",
  "developer",
  "qa",
  "security_engineer",
  "devops_engineer",
  "data_engineer",
  "scrum_master",
  "custom",
] as const;

export const ROLE_META: Record<PlatformRole, RoleMeta> = {
  org_admin: {
    label: "Organization Admin",
    shortLabel: "Org Admin",
    scope: "organization",
    tier: "governance",
    oneLiner:
      "Creates business units and appoints their admins; sets the organization budget and org-wide policy.",
    governanceOnly: true,
    prdSection: "§15.2",
  },
  bu_admin: {
    label: "Business Unit Admin",
    shortLabel: "BU Admin",
    scope: "business_unit",
    tier: "governance",
    oneLiner:
      "Runs one business unit: its budget, connections, members, and project creation.",
    governanceOnly: true,
    prdSection: "§15.3",
  },
  contributor: {
    label: "Contributor",
    shortLabel: "Contributor",
    scope: "business_unit",
    tier: "delivery",
    oneLiner:
      "Belongs to a business unit and is waiting for its admin to say what they do there.",
    governanceOnly: false,
    prdSection: "— (platform addition: the org-level appointment, see ORG_ASSIGNABLE_ROLES)",
  },
  project_admin: {
    label: "Project Admin",
    shortLabel: "Project Admin",
    scope: "project",
    tier: "delivery",
    oneLiner:
      "Runs one project; selects its connections; fallback approver on every agent.",
    governanceOnly: false,
    prdSection: "§15.4",
  },
  ba: {
    label: "BA (Business Analyst)",
    shortLabel: "BA",
    scope: "project",
    tier: "delivery",
    oneLiner: "Owns the Requirements agent.",
    governanceOnly: false,
    prdSection: "§15.5",
  },
  architect: {
    label: "Architect",
    shortLabel: "Architect",
    scope: "project",
    tier: "delivery",
    oneLiner: "Owns Design; approves Development and Code Review.",
    governanceOnly: false,
    prdSection: "§15.6",
  },
  developer: {
    label: "Developer",
    shortLabel: "Developer",
    scope: "project",
    tier: "delivery",
    oneLiner: "Builds in Development; requests code review. Never self-approves.",
    governanceOnly: false,
    prdSection: "§15.7",
  },
  qa: {
    label: "QA / Tester",
    shortLabel: "QA",
    scope: "project",
    tier: "delivery",
    oneLiner: "Owns Testing.",
    governanceOnly: false,
    prdSection: "§15.8",
  },
  security_engineer: {
    label: "Security Engineer",
    shortLabel: "Security Eng",
    scope: "project",
    tier: "delivery",
    oneLiner: "Owns Security; standing view of traces and the audit trail.",
    governanceOnly: false,
    prdSection: "§15.9",
  },
  devops_engineer: {
    label: "DevOps Engineer",
    shortLabel: "DevOps Eng",
    scope: "project",
    tier: "delivery",
    oneLiner: "Owns Deployment; requests tooling in Development.",
    governanceOnly: false,
    prdSection: "§15.10",
  },
  data_engineer: {
    label: "Data Engineer",
    shortLabel: "Data Eng",
    scope: "project",
    tier: "delivery",
    oneLiner: "Owns the Data Engineering agent (Track 5).",
    governanceOnly: false,
    prdSection: "§15.11",
  },
  scrum_master: {
    label: "Scrum Master",
    shortLabel: "Scrum Master",
    scope: "project",
    tier: "delivery",
    oneLiner:
      "Coordinates the team's flow across every agent stage; observes but owns no single gate.",
    governanceOnly: false,
    prdSection: "— (platform addition, beyond the original PRD §33.1 roster)",
  },
  custom: {
    label: "Custom",
    shortLabel: "Custom",
    scope: "configurable",
    tier: "delivery",
    oneLiner:
      "A governed bundle of permissions and agent access, composed by an admin within its own scope.",
    governanceOnly: false,
    prdSection: "§15.12",
  },
};

// ─── Who may appoint whom ─────────────────────────────────────────────────────

/**
 * The only two roles an Organization Admin appoints.
 *
 * Onboarding used to offer all eleven working roles, which asked the wrong
 * person the wrong question: whether a new joiner is a Developer or a QA is a
 * fact about a team the Org Admin does not run, and they were guessing at it
 * for every hire in the organisation. So onboarding answers only what the Org
 * Admin actually knows — does this person RUN a business unit, or do they work
 * in one — and the unit's own admin, who does know, fills in the rest.
 *
 *   bu_admin     runs a unit. Its unit is optional at onboarding: appointing
 *                the person and deciding which unit they get are separately
 *                timed decisions, and blocking the first on the second is how
 *                an admin ends up parked in a unit to be moved later.
 *   contributor  works in a unit, which is therefore REQUIRED — a contributor
 *                with no unit belongs to nobody, so nobody is prompted to give
 *                them a role and they sit invisible with no access at all.
 *
 * Every other role is a Business Unit role, granted by that unit's admin —
 * see `BUSINESS_UNIT_ASSIGNABLE_BUILTIN_ROLES` in hooks/use-assignable-roles.
 */
export const ORG_ASSIGNABLE_ROLES: readonly PlatformRole[] = ["bu_admin", "contributor"];

/** Is this an org-level appointment (`ORG_ASSIGNABLE_ROLES`) rather than a
 *  role held inside a unit? Org Admin writes are checked against it. */
export function isOrgAssignableRole(role: string): role is PlatformRole {
  return (ORG_ASSIGNABLE_ROLES as readonly string[]).includes(role);
}

/**
 * Is this a role a BUSINESS UNIT ADMIN may onboard somebody as, inside their unit?
 *
 * The delivery-tier built-ins minus `contributor` and `custom` — the mirror of
 * UNIT_ASSIGNABLE in backend/shared/routers/onboarding.py, and the same set as
 * BUSINESS_UNIT_ASSIGNABLE_BUILTIN_ROLES in hooks/use-assignable-roles.ts (kept here
 * as a predicate so lib/mock can check it without importing a hooks module).
 *
 * `bu_admin` is excluded because it is an ORG-level appointment; `contributor`
 * because it files a role_assignment request addressed to this very caller.
 */
export function isUnitAssignableRole(role: string): role is PlatformRole {
  return (
    role in ROLE_META &&
    ROLE_META[role as PlatformRole].tier === "delivery" &&
    role !== "contributor" &&
    role !== "custom"
  );
}

/**
 * The roles a contributor BORROWED from another Business Unit may hold.
 *
 * Project-scoped roles, plus Project Admin — someone can be lent to run a
 * project, which is still a project-level job. Both admin tiers are absent and
 * always will be: a governance role is never a project member at all, so
 * lending one would be lending authority over a unit rather than work inside a
 * project.
 *
 * Lives here rather than beside the cross-unit transaction because the request
 * dialog needs it, and reaching into `lib/mock/*` from a client component would
 * pull the whole fixture graph into the browser bundle.
 */
export const CROSS_BU_ASSIGNABLE_ROLES: readonly PlatformRole[] = ROLE_ORDER.filter(
  (r) => r === "project_admin" || (ROLE_META[r].scope === "project" && r !== "custom"),
);

export function isCrossBuAssignableRole(role: string): boolean {
  return (CROSS_BU_ASSIGNABLE_ROLES as readonly string[]).includes(role);
}

/**
 * Does a Business Unit membership still need its admin to act?
 *
 * `contributor` is a placeholder, not a job: it records that the Org Admin put
 * this person in the unit and that nobody has yet said what they do there. The
 * unit's pending-assignment queue is exactly the set of memberships where this
 * is true.
 */
export function awaitsBusinessUnitRole(role: string): boolean {
  return role === "contributor";
}

// ─── Ownership matrix — role × agent (PRD §14.7) ──────────────────────────────

/**
 * How a role relates to an agent.
 *
 *  - `owner`    Owns it: chats with it AND approves its Consequential actions
 *               and Sign-offs. Project Admin is `owner` on every agent as the
 *               risk-aware fallback.
 *  - `primary`  The agent's primary human user; also its approver.
 *  - `build`    Does the hands-on work, but its consequential actions are
 *               approved by the owner (Developer on Development).
 *  - `requests` Can request something of the agent but neither runs nor
 *               approves it (Developer requesting code review).
 *  - `use`      May chat / run its Safe capabilities only.
 *  - `none`     No involvement.
 */
export type Involvement = "owner" | "primary" | "build" | "requests" | "use" | "none";

// `ALL_USE` is deliberately gone. Every delivery role was built from it and
// then subtracted, which is the wrong default for an access table: a role
// nobody thought about reached everything. Roles are composed from ALL_NONE
// upward now, so a forgotten grant denies rather than permits.

const ALL_NONE: Record<Phase, Involvement> = {
  requirements: "none",
  design: "none",
  plan: "none",
  development: "none",
  review: "none",
  security: "none",
  testing: "none",
  deployment: "none",
  documentation: "none",
  discovery: "none",
  strategy: "none",
  migration_mapping: "none",
  validation: "none",
  data_engineering: "none",
};

const ALL_OWNER: Record<Phase, Involvement> = {
  requirements: "owner",
  design: "owner",
  plan: "owner",
  development: "owner",
  review: "owner",
  security: "owner",
  testing: "owner",
  deployment: "owner",
  documentation: "owner",
  discovery: "owner",
  strategy: "owner",
  migration_mapping: "owner",
  validation: "owner",
  data_engineering: "owner",
};

/**
 * Role × agent involvement, per PRD §14.7 plus the track-specific agents from
 * §23 (Modernization), §24 (RPA/Infra) and §25 (Data engineering), whose
 * owners the track stage tables name explicitly:
 *   Discovery & Assessment → Architect · Strategy → Architect
 *   Migration Mapping → Architect · Validation → QA/Tester
 *   Data Engineering → Data Engineer
 *
 * Organization Admin and Business Unit Admin are absent by design: they have
 * NO agent access at all — they are governance-only (PRD §14.8).
 */
export const AGENT_OWNERSHIP: Record<PlatformRole, Record<Phase, Involvement>> = {
  // Governance tier — no agent access whatsoever (PRD §14.8).
  org_admin: { ...ALL_NONE },
  bu_admin: { ...ALL_NONE },

  // Not a job yet, so not an access level yet. A Contributor reaches nothing
  // until their Business Unit Admin says which delivery role they hold; that
  // assignment replaces this placeholder and brings its access with it.
  contributor: { ...ALL_NONE },

  // Fallback approver on every agent, subject to the risk-aware limits in §14.5.
  project_admin: { ...ALL_OWNER },

  // ── Delivery roles ──────────────────────────────────────────────────────
  //
  // SPREAD FROM `ALL_NONE`, NOT `ALL_USE`. Every delivery role used to start
  // from "use everything" and subtract, which made least privilege something
  // you had to remember to apply — and mostly it wasn't: a BA reached all
  // thirteen agents. Starting from nothing and adding makes each role's list
  // exactly what it is allowed to touch, and makes an omission fail closed.
  //
  // TWO INVARIANTS hold this table together, and `agent-ownership.test.ts`
  // enforces both:
  //
  //   1. A phase's owner (`AGENT_OWNER_ROLE`) always reaches its own agent.
  //      An owner who cannot open the agent they sign off for is a gate with
  //      nobody behind it.
  //   2. Documentation is reachable by every delivery role. Its acceptance is
  //      automatic and every role writes into it; it is the one genuinely
  //      shared surface.

  // Requirements is the BA's. Design is downstream of their output and they
  // stay in it; nothing else is theirs to drive.
  ba: {
    ...ALL_NONE,
    requirements: "primary",
    design: "use",
    plan: "use",
    development: "use",
    review: "use",
    security: "use",
    testing: "use",
    deployment: "use",
    documentation: "use",
  },

  // The broadest delivery role, because it owns the most gates — Design,
  // Development and Code Review, plus the three modernization-track agents.
  //
  // Code Review is here as OWNER even though it was not in the requested list:
  // `AGENT_OWNER_ROLE.review` is `architect`, so removing it would leave Code
  // Review's sign-off routed to a role that cannot open it. Reassigning that
  // gate is a separate decision from narrowing access, and not one to make as
  // a side effect.
  architect: {
    ...ALL_NONE,
    requirements: "use",
    design: "primary",
    plan: "primary",
    development: "primary",
    review: "primary",
    security: "use",
    testing: "use",
    deployment: "use",
    documentation: "use",
    discovery: "primary",
    strategy: "primary",
    migration_mapping: "primary",
  },

  // Builds Development; the Architect approves it, so this is `build` and not
  // `primary` — never self-approval.
  developer: {
    ...ALL_NONE,
    requirements: "use",
    development: "build",
    review: "requests",
    security: "use",
    testing: "use",
    documentation: "use",
  },

  // Owns Testing, and Validation on the modernization tracks. Reads
  // Development because that is what it tests.
  qa: {
    ...ALL_NONE,
    requirements: "use",
    development: "use",
    security: "use",
    testing: "primary",
    documentation: "use",
    validation: "primary",
  },

  // Owns Security. Reads the code it assesses and the review it feeds.
  security_engineer: {
    ...ALL_NONE,
    requirements: "use",
    design: "use",
    plan: "use",
    development: "use",
    review: "use",
    security: "primary",
    deployment: "use",
  },

  // Owns Deployment. Reads Development because that is what it ships.
  devops_engineer: {
    ...ALL_NONE,
    development: "requests",
    security: "use",
    testing: "use",
    deployment: "primary",
    documentation: "use",
  },

  // Owns Data Engineering. Reads Development for the pipelines' surroundings.
  data_engineer: {
    ...ALL_NONE,
    requirements: "use",
    design: "use",
    security: "use",
    testing: "use",
    documentation: "use",
    data_engineering: "primary",
  },

  // Cross-cutting coordinator who owns no gate. Coordination is not agent
  // operation, so visibility into the two stages that describe the work is
  // the whole of it — the run history and approvals queue are where a Scrum
  // Master actually watches progress, and neither needs agent access.
  scrum_master: {
    ...ALL_NONE,
    requirements: "use",
    documentation: "use",
  },

  // Composed per assignment — the builder picks exact agent access (PRD §14.9).
  custom: { ...ALL_NONE },
};

/**
 * The role that owns an agent's gate — i.e. who a Consequential action or
 * Sign-off routes to (PRD §14.7, §33.2). Approvals go *sideways* to the
 * agent's owner, never up to a governance tier.
 */
export const AGENT_OWNER_ROLE: Record<Phase, PlatformRole> = {
  requirements: "ba",
  design: "architect",
  // The one agent scrum_master owns rather than merely uses.
  plan: "scrum_master",
  development: "architect", // Developer builds; Architect approves — never self-approval.
  review: "architect",
  security: "security_engineer",
  testing: "qa",
  deployment: "devops_engineer",
  documentation: "project_admin", // Acceptance is automatic; override exists.
  discovery: "architect",
  strategy: "architect",
  migration_mapping: "architect",
  validation: "qa",
  data_engineering: "data_engineer",
};

/** Display label for the role a gate is waiting on. */
export function ownerRoleLabel(phase: Phase): string {
  return ROLE_META[AGENT_OWNER_ROLE[phase]].label;
}

/**
 * Project Admin is the fallback approver on every agent for its own project
 * (PRD §33.2, "The fallback — so work never stalls"). A fallback approval is
 * always audited *as* a fallback, never disguised as the owner's decision.
 */
export const FALLBACK_APPROVER: PlatformRole = "project_admin";

/** Roles that can chat with an agent (own, build, request or use it). */
export function canUseAgent(role: PlatformRole, phase: Phase): boolean {
  return AGENT_OWNERSHIP[role][phase] !== "none";
}

/** Roles that approve an agent's gates — owner or primary only. */
export function canApproveAgent(role: PlatformRole, phase: Phase): boolean {
  const involvement = AGENT_OWNERSHIP[role][phase];
  return involvement === "owner" || involvement === "primary";
}

/**
 * Tier separation guardrail (PRD §14.5, §14.6) — mirrored client-side for UX.
 *
 * Two roles conflict only when they would be held **in the same scope**: that
 * is what would let someone approve their own work. Holding a governance role
 * in one scope and a delivery role in another is legitimate and common — a BU
 * Admin for Payments who is also a BA on a Lending project governs neither
 * their own delivery nor delivers into their own governance.
 */
export function tiersConflict(a: PlatformRole, b: PlatformRole): boolean {
  return ROLE_META[a].tier !== ROLE_META[b].tier;
}

/** One scope a person holds roles in, for tier-conflict checking. */
export interface ScopedRoles {
  /** Stable key for the scope — a workspace or project id. */
  scopeId: string;
  roles: string[];
}

/**
 * The scopes in which a person holds BOTH a governance and a delivery role —
 * the only genuine separation-of-duties violation. Returns the offending
 * scope ids; an empty array means the person's bindings are all legitimate,
 * however many tiers they span in total.
 *
 * Unknown role strings (custom roles, or a deleted role still referenced by an
 * old binding) carry no tier and are ignored rather than treated as suspect.
 */
/**
 * A person administers AT MOST ONE Business Unit.
 *
 * "Runs one business unit" is the role's whole definition (PRD §15.3) — its
 * budget, its connections, its members, its project creation. Someone holding
 * it twice is an org-wide administrator without the title, accountable for two
 * budgets and able to move work between them with nobody above either.
 *
 * SEPARATELY, a governance role is never a project member at all — not in its
 * own unit and not in anyone else's (`projectMembershipBlock` in
 * lib/mock/project-membership-fixtures.ts). This function is only about holding
 * `bu_admin` TWICE; the project rule is enforced where projects are joined.
 *
 * Returns the unit they already administer, or null when the grant is fine.
 */
export function buAdminElsewhere(
  bindings: readonly { scopeId: string; scopeName?: string; role: string }[],
  targetScopeId: string,
  roleName: string,
): { scopeId: string; scopeName?: string } | null {
  if (roleName !== "bu_admin") return null;
  const held = bindings.find(
    (b) => b.role === "bu_admin" && String(b.scopeId) !== String(targetScopeId),
  );
  return held ? { scopeId: held.scopeId, scopeName: held.scopeName } : null;
}

export function scopeTierConflicts(scopes: ScopedRoles[]): string[] {
  return scopes
    .filter((s) => {
      const tiers = new Set(
        s.roles
          .filter((r): r is PlatformRole => r in ROLE_META)
          .map((r) => ROLE_META[r].tier),
      );
      return tiers.has("governance") && tiers.has("delivery");
    })
    .map((s) => s.scopeId);
}
