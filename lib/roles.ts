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

const ALL_USE: Record<Phase, Involvement> = {
  requirements: "use",
  design: "use",
  development: "use",
  review: "use",
  security: "use",
  testing: "use",
  deployment: "use",
  documentation: "use",
  discovery: "use",
  strategy: "use",
  migration_mapping: "use",
  validation: "use",
  data_engineering: "use",
};

const ALL_NONE: Record<Phase, Involvement> = {
  requirements: "none",
  design: "none",
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

  // Fallback approver on every agent, subject to the risk-aware limits in §14.5.
  project_admin: { ...ALL_OWNER },

  ba: { ...ALL_USE, requirements: "primary" },

  architect: {
    ...ALL_USE,
    design: "primary",
    development: "primary",
    review: "primary",
    discovery: "primary",
    strategy: "primary",
    migration_mapping: "primary",
  },

  developer: {
    ...ALL_USE,
    design: "none",
    development: "build",
    review: "requests",
    deployment: "none",
  },

  qa: {
    ...ALL_USE,
    design: "none",
    review: "none",
    testing: "primary",
    validation: "primary",
    deployment: "none",
  },

  security_engineer: {
    ...ALL_USE,
    security: "primary",
    testing: "none",
    documentation: "none",
  },

  devops_engineer: {
    ...ALL_USE,
    requirements: "none",
    design: "none",
    development: "requests",
    review: "none",
    deployment: "primary",
    data_engineering: "none",
  },

  data_engineer: {
    ...ALL_USE,
    development: "none",
    review: "none",
    deployment: "none",
    data_engineering: "primary",
  },

  // Cross-cutting coordinator: visibility into every stage, owns none of them.
  scrum_master: { ...ALL_USE },

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
