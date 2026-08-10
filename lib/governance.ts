import type { GovernanceApprovalType } from "@/lib/schemas/governance-approval";
import type { ProfileScope } from "@/lib/schemas/agent-profiles";
import type { PlatformRole } from "@/lib/roles";

/**
 * Which role a governance approval routes to. Deliberately a separate,
 * small map — NOT folded into `AGENT_OWNER_ROLE`/`GATE_POLICY`
 * (`lib/agents.ts`, `lib/roles.ts`), which are keyed by `Phase` and only
 * ever route to delivery-tier roles ("approvals go sideways, never up to a
 * governance tier" is explicit there). Project-creation and
 * model-credential approvals are the first case where a gate genuinely
 * routes to a governance-tier role.
 */
export const GOVERNANCE_APPROVER_ROLE: Record<GovernanceApprovalType, PlatformRole> = {
  project_creation: "bu_admin",
  model_credential: "bu_admin",
  // A BU Admin requesting more budget for their own business unit needs the
  // Org Admin above them, not a peer — this type goes a tier further up.
  budget_increase: "org_admin",
  // The Project Admin (owner) and Org Admin can archive a project directly;
  // a BU Admin archiving a project they don't own needs the Org Admin's
  // sign-off — same escalation shape as budget_increase.
  project_archive: "org_admin",
  // Agent Studio's cascade — see AGENT_DEFAULT_OWNER_ROLE below, which this
  // mirrors for the three tiers that need governance approval.
  agent_default_org: "org_admin",
  agent_default_workspace: "bu_admin",
  agent_default_project: "project_admin",
  // ── Hand-raised types ─────────────────────────────────────────────────────
  // These are TIER-routed, not type-routed: who decides depends on who asked,
  // so `lib/requests/routing.ts::initialApproverRole` computes the real
  // approver and writes it to the request's `currentApproverRole`. The entries
  // below are the floor used when no requester role is known — they keep this
  // Record exhaustive so a new type cannot be added without a decision here.
  connector_access: "project_admin",
  mcp_server: "project_admin",
  access_request: "project_admin",
  user_onboarding: "project_admin",
  other: "project_admin",
  // Type-routed, and the only type that routes DOWNWARD. The Organization
  // Admin raises it when they place a Contributor in a unit; the unit's admin
  // is the only person who can say what that person does there. Tier routing
  // would compute "one above the requester" and find nobody, because the
  // requester is already at the top.
  role_assignment: "bu_admin",
  // The one hand-raised type that IS type-routed: onboarding a provider is an
  // organization-wide act whoever asks for it, so it goes to the Org Admin
  // rather than one tier above the requester. A Business Unit Admin asking
  // would land there anyway; this makes it true for everyone.
  model_provider_access: "org_admin",
  // Routed to a Business Unit Admin — but a SPECIFIC one, and not the asker's.
  // The request carries the contributor's parent unit as its `workspaceId`, so
  // the queue's own scope filter (`canReadGovernanceApproval`) lands it with
  // that unit's admin. They are the only person who can lend their own people.
  cross_bu_assignment: "bu_admin",
  // Type-routed to stage one's approver. The second stage is the agent's own
  // owner, which depends on the PHASE being asked for and so cannot live in a
  // map keyed by type — `lib/requests/routing.ts::agentAccessApprover` derives
  // it, and the request carries the stage it has reached.
  agent_access: "project_admin",
};

/**
 * Who owns each tier of Agent Studio's Org → Business Unit → Project →
 * Personal cascade — i.e. who can publish a change to that tier's shared
 * default immediately, with no approval. Anyone else proposing a change to a
 * tier routes to a `GovernanceApproval` of the matching `agent_default_*`
 * type instead (see lib/mock/agent-profile-fixtures.ts). `user` has no
 * owner role because it isn't a shared tier — it's always the individual
 * person's own setting, so it's never something to "propose a change to".
 */
export const AGENT_DEFAULT_OWNER_ROLE: Record<ProfileScope, PlatformRole | null> = {
  org: "org_admin",
  workspace: "bu_admin",
  project: "project_admin",
  user: null,
};

/**
 * May this role publish a default at this tier directly?
 *
 * The three shared tiers answer from `AGENT_DEFAULT_OWNER_ROLE`: you publish
 * your own tier and propose to the one above it. `user` is the exception in
 * both directions — everyone owns their own personal override, EXCEPT the two
 * governance roles, who never run an agent (PRD §14.8) and so would be writing
 * instructions that can never take effect, outside the cascade everyone else
 * can see and inherit from. They own Organization and Business Unit; that is
 * where their instructions go.
 *
 * Shared by the client gate and BOTH server runtimes deliberately. A rule the
 * UI merely hides is a suggestion — see the note on `raisableTypesFor`
 * (lib/requests/routing.ts), which was scoped the same way for the same
 * reason.
 */
export function canPublishAtTier(
  role: PlatformRole | null,
  scope: ProfileScope,
): boolean {
  if (role === null) return false;
  if (scope === "user") return role !== "org_admin" && role !== "bu_admin";
  return role === AGENT_DEFAULT_OWNER_ROLE[scope];
}

/** `agent_default_org` | `agent_default_workspace` | `agent_default_project`
 *  for the three tiers that can be proposed-and-approved; `user` never is. */
export function agentDefaultApprovalType(
  scope: Exclude<ProfileScope, "user">,
): Extract<GovernanceApprovalType, `agent_default_${string}`> {
  return `agent_default_${scope}` as Extract<GovernanceApprovalType, `agent_default_${string}`>;
}
