/**
 * Where a REQUEST goes, and where it goes next.
 *
 * ─────────────────────────────────────────────────────────────────────────────
 * REQUESTS ARE NOT APPROVALS. PRD §33.2 sets them apart on every axis, and this
 * module implements only the first column:
 *
 *                  Request                        Approval
 *   Triggered by   a person needing something     an agent about to act
 *   Routes to      the first tier that can grant  the agent's OWNING role
 *   Direction      UPWARD: PA → BU → Org          SIDEWAYS, never to governance
 *   Fallback       none — it climbs               Project Admin, audited as such
 *
 * Agent-gate routing lives in `lib/agents.ts` (`GATE_POLICY`, `AGENT_OWNER_ROLE`)
 * and must stay there. Merging the two would breach "approvals never route to a
 * governance tier" and the "no approval laundering" guardrail (§33.2), which
 * exist so a consequential action cannot be signed off by someone who never had
 * the standing to judge it.
 * ─────────────────────────────────────────────────────────────────────────────
 */
import { GOVERNANCE_APPROVER_ROLE } from "@/lib/governance";
import { AGENT_OWNER_ROLE, ROLE_META, type PlatformRole } from "@/lib/roles";
import type { Phase } from "@/lib/schemas/enums";
import type { GovernanceApprovalType } from "@/lib/schemas/governance-approval";

/**
 * The ladder a request climbs, lowest rung first.
 *
 * Everything below `project_admin` — every delivery contributor — enters at the
 * bottom, which is why the chain starts there rather than enumerating twelve
 * roles: a BA and a Developer escalate identically, and encoding each of them
 * separately would only create twelve places for the ladder to drift.
 */
export const REQUEST_ESCALATION_CHAIN: readonly PlatformRole[] = [
  "project_admin",
  "bu_admin",
  "org_admin",
];

/**
 * The seven types whose approver is fixed by the TYPE, not the requester —
 * a project creation goes to the BU Admin no matter who filed it. Hand-raised
 * types are absent and fall through to tier routing.
 */
const TYPE_ROUTED = new Set<GovernanceApprovalType>([
  "project_creation",
  // `model_credential` is NO LONGER type-routed. Its meaning is "make a model
  // available to my project", and who can do that depends on who is asking:
  //
  //   contributor    → Project Admin, who ticks the box on the project's own
  //                    model selection (they hold `model:manage`).
  //   Project Admin  → Business Unit Admin, because the model they lack is one
  //                    the unit was never granted.
  //
  // Fixed to `bu_admin` it always skipped the Project Admin — sending a
  // contributor's "can I use Opus on this project" to a tier that would have
  // to ask the Project Admin anyway. Tier routing lands both cases correctly;
  // the escalation still climbs if the first rung cannot grant it.
  //
  // `budget_increase` is NO LONGER type-routed either, for the same reason.
  // Pinned to `org_admin` it sent a Project Admin asking for their own
  // project's headroom straight past the Business Unit Admin — who owns the
  // unit's cap and is the one with the context to answer it. Tier-routed:
  //
  //   contributor    → Project Admin
  //   Project Admin  → Business Unit Admin
  //   BU Admin       → Org Admin (a peer is no use for their own unit's cap)
  "project_archive",
  "agent_default_org",
  "agent_default_workspace",
  "agent_default_project",
  // Onboarding a provider is an organization-wide act whoever asks — see
  // GOVERNANCE_APPROVER_ROLE.
  "model_provider_access",
  // Always starts with the Project Admin, whoever raised it — stage one of two.
  // See AGENT_ACCESS_STAGES.
  "agent_access",
  // The unit's own admin, always. Tier routing climbs from the requester, and
  // this request is raised by the Organization Admin, who has nobody above
  // them — the answer is one tier DOWN, which only the type knows.
  "role_assignment",
  // SIDEWAYS, not up: to the Business Unit Admin who owns the contributor being
  // asked for. Climbing from the requester would land it with the borrowing
  // unit's own admin, who cannot lend somebody else's person.
  "cross_bu_assignment",
]);

/**
 * The one type answered twice, and by whom.
 *
 * The Project Admin goes first because theirs is the cheaper question — "should
 * this person be doing this work" — and a no there saves the agent's owner a
 * decision entirely. The owner goes second because theirs is the one that
 * cannot be delegated: §44.5 keeps agent sign-offs with the roles that own
 * them, and a Project Admin who could grant an agent outright would be
 * approving on the owner's behalf.
 */
export const AGENT_ACCESS_STAGES = ["project_admin", "agent_owner"] as const;
export type AgentAccessStage = (typeof AGENT_ACCESS_STAGES)[number];

/**
 * Who owns an agent — the role that decides stage two.
 *
 * Deliberately a re-export of `AGENT_OWNER_ROLE` rather than a fresh
 * derivation over `AGENT_OWNERSHIP`. The two would agree today and that is
 * exactly the trap: the canonical map encodes decisions the involvement table
 * cannot express — Development is BUILT by the Developer and APPROVED by the
 * Architect, and Documentation's owner is the Project Admin because acceptance
 * there is automatic. A second source for "who owns this agent" would drift
 * from the one the gates already use.
 */
export function agentOwnerRole(phase: Phase): PlatformRole {
  return AGENT_OWNER_ROLE[phase];
}

/**
 * The approver for a given stage of an agent-access request.
 *
 * Kept next to `agentOwnerRole` rather than inlined at the call sites so the
 * two stages cannot drift: the queue, the decide step and the "who sees this"
 * preview all have to name the same person.
 */
export function agentAccessApprover(stage: AgentAccessStage, phase: Phase): PlatformRole {
  return stage === "project_admin" ? "project_admin" : agentOwnerRole(phase);
}

/**
 * The stage after this one, or null when the request is fully decided.
 *
 * Needs the phase, because whether a second stage EXISTS depends on it. Where
 * the Project Admin is themselves the agent's owner — Documentation, whose
 * acceptance is automatic — stage one already was the owner's decision, and
 * advancing would hand the request back to the person who just made it. One
 * approver, asked once.
 */
export function nextAgentAccessStage(
  stage: AgentAccessStage | null,
  phase: Phase,
): AgentAccessStage | null {
  if (stage !== "project_admin") return null;
  return agentOwnerRole(phase) === "project_admin" ? null : "agent_owner";
}

/**
 * WHAT EACH TIER MAY ASK FOR.
 *
 * One rule generates all three lists: YOU REQUEST WHAT YOU CANNOT GRANT
 * YOURSELF. A flat list shown to everyone got this wrong in both directions —
 * it offered a Developer "Project creation" and "Budget headroom", neither of
 * which is theirs to ask at that level, while giving a Business Unit Admin the
 * project-scoped "Model credential" when the thing they actually lack is an
 * org-wide provider.
 *
 * The same noun therefore appears at two tiers meaning two different asks, and
 * that is deliberate rather than duplication:
 *
 *   model_credential        "grant this model to my project"     (below the BU)
 *   model_provider_access   "onboard this provider org-wide"     (BU → Org)
 *
 *   user_onboarding at any tier means the same thing — bring someone onto the
 *   platform who is not on it yet. A Project Admin already holds `member:manage`
 *   and can add an EXISTING person to their project without asking anyone; what
 *   they cannot do is create the person.
 */
const CONTRIBUTOR_RAISABLE: readonly GovernanceApprovalType[] = [
  "agent_access",
  "access_request",
  "model_credential",
  "connector_access",
  "mcp_server",
  "user_onboarding",
  "other",
];

const PROJECT_ADMIN_RAISABLE: readonly GovernanceApprovalType[] = [
  "user_onboarding",
  "model_credential",
  "connector_access",
  "mcp_server",
  "budget_increase",
  "project_creation",
  "access_request",
  "other",
];

const BU_ADMIN_RAISABLE: readonly GovernanceApprovalType[] = [
  "model_provider_access",
  "user_onboarding",
  "connector_access",
  "budget_increase",
  "access_request",
  "other",
];

/**
 * The types this role may raise, in the order the picker offers them —
 * likeliest ask first, `other` always last.
 *
 * Enforced server-side too (`app/api/governance-approvals/route.ts`): a picker
 * that merely hides an option is a suggestion, and the whole point of scoping
 * these is that a request filed at the wrong tier wastes the approver's time
 * before it wastes the requester's.
 */
export function raisableTypesFor(role: PlatformRole | null): readonly GovernanceApprovalType[] {
  if (role === null || role === "org_admin") return [];
  if (role === "bu_admin") return BU_ADMIN_RAISABLE;
  if (role === "project_admin") return PROJECT_ADMIN_RAISABLE;
  return CONTRIBUTOR_RAISABLE;
}

export function canRaiseType(
  role: PlatformRole | null,
  type: GovernanceApprovalType,
): boolean {
  return raisableTypesFor(role).includes(type);
}

/**
 * May this role raise a request at all?
 *
 * False for the Organization Admin alone, and not as a restriction so much as
 * an arithmetic fact: the chain ends at them, so a request they raised would
 * have no one to decide it. They are the ceiling, so they only ever receive.
 */
export function canRaiseRequest(role: PlatformRole | null): boolean {
  return role !== null && role !== "org_admin";
}

/** The next rung up, or null at the top of the ladder. */
export function nextApproverRole(current: PlatformRole | null): PlatformRole | null {
  if (current === null) return REQUEST_ESCALATION_CHAIN[0] ?? null;
  const i = REQUEST_ESCALATION_CHAIN.indexOf(current);
  // A role off the ladder (any delivery contributor) enters at the bottom.
  if (i === -1) return REQUEST_ESCALATION_CHAIN[0] ?? null;
  return REQUEST_ESCALATION_CHAIN[i + 1] ?? null;
}

/**
 * Who decides this request first.
 *
 * Two rules, in order:
 *  1. A type-routed request goes where its type says.
 *  2. Anything else goes one tier above whoever raised it.
 *
 * Then one override on top of both: if that lands on the requester's own role,
 * it climbs. "No one approves their own request — it escalates instead"
 * (§33.2) is not a UI courtesy; routing has to enforce it, or a BU Admin filing
 * a BU-Admin-routed request would silently become their own approver.
 */
export function initialApproverRole(
  type: GovernanceApprovalType,
  requesterRole: PlatformRole | null,
): PlatformRole | null {
  const routed = TYPE_ROUTED.has(type)
    ? GOVERNANCE_APPROVER_ROLE[type]
    : nextApproverRole(requesterRole);

  /**
   * The bump exists so nobody decides their own tier's ask — a Business Unit
   * Admin's project-creation request must not land back with a Business Unit
   * Admin, because that would be them.
   *
   * `cross_bu_assignment` is the exception, and the only one: its approver is a
   * DIFFERENT unit's admin — the one who owns the contributor being asked for —
   * identified by the request's `workspaceId` rather than by tier. Bumping it
   * sent a Business Unit Admin's ask to the Organization Admin, who has no
   * standing to lend another unit's people and every reason not to be asked.
   * Same rung, different unit, and the peer is not the requester.
   */
  const samePersonRisk = type !== "cross_bu_assignment";
  if (samePersonRisk && routed !== null && routed === requesterRole) {
    return nextApproverRole(routed);
  }
  return routed;
}

/**
 * HOW FAR A REQUEST MAY CLIMB, given who raised it.
 *
 * A contributor's request stops at their Project Admin. The Project Admin owns
 * the project the ask is about — its members, its models, its tools — and is
 * the person who either grants it or decides it is not worth pursuing. Letting
 * it climb past them on its own would put an ask about one project in front of
 * an Org Admin who has no context for it, and would route around the person
 * accountable for that project rather than through them.
 *
 * That does NOT dead-end an ask the Project Admin cannot grant. When what they
 * lack is a grant from above — a model the business unit was never given — they
 * raise their own request for it (PROJECT_ADMIN_RAISABLE), which climbs from
 * their tier. The escalation is explicit and owned rather than automatic and
 * anonymous, and the contributor's request stays where someone can answer it.
 *
 * The two admin tiers keep the full ladder: their asks are genuinely about the
 * tier above them, so there is no one below to route through.
 */
export function escalationCeilingFor(requesterRole: PlatformRole | null): PlatformRole {
  if (requesterRole === null) return "org_admin";
  const i = REQUEST_ESCALATION_CHAIN.indexOf(requesterRole);
  // Off the ladder = a delivery contributor. Their ceiling is the bottom rung.
  return i === -1 ? "project_admin" : "org_admin";
}

/**
 * The full ladder this request will climb from where it now sits — for the
 * form's "who will see this" preview and the detail view's remaining path.
 * Showing the whole route up front is what makes an escalation later read as
 * the process working rather than the request being passed around.
 *
 * Truncated at the requester's ceiling: showing a contributor
 * "Project Admin → Business Unit Admin → Organization Admin" promised a climb
 * that will not happen, which is worse than showing one name — it sets the
 * expectation that waiting is enough.
 */
export function approverChainFrom(
  role: PlatformRole | null,
  requesterRole?: PlatformRole | null,
): PlatformRole[] {
  const ceiling = requesterRole === undefined ? "org_admin" : escalationCeilingFor(requesterRole);
  const chain: PlatformRole[] = [];
  let cur = role;
  while (cur !== null) {
    chain.push(cur);
    if (cur === ceiling) break;
    cur = nextApproverRole(cur);
  }
  return chain;
}

export interface DecideEligibility {
  allowed: boolean;
  /** Why not — shown to the viewer rather than a disabled button with no cause. */
  reason?: string;
}

/**
 * May this viewer decide this request?
 *
 * Three gates, and the order is deliberate: the self-approval block comes
 * first, so a Project Admin who raised their own request is told *that* rather
 * than "not your queue" — the second message is true but misleading, and would
 * send them looking for a permission they already hold.
 */
export function canDecideRequest(args: {
  currentApproverRole: PlatformRole | null;
  requestedById: string | null | undefined;
  status: string;
  viewerRole: PlatformRole | null;
  viewerIdentityId: string | null;
}): DecideEligibility {
  const { currentApproverRole, requestedById, status, viewerRole, viewerIdentityId } = args;

  if (status !== "submitted" && status !== "pending_review" && status !== "escalated") {
    return { allowed: false, reason: "This request is already closed." };
  }
  if (requestedById && viewerIdentityId && requestedById === viewerIdentityId) {
    return { allowed: false, reason: "You raised this request — it escalates rather than self-approving." };
  }
  if (viewerRole === null || currentApproverRole === null) {
    return { allowed: false, reason: "This request is not waiting on you." };
  }
  if (viewerRole !== currentApproverRole) {
    return {
      allowed: false,
      reason: `Waiting on the ${ROLE_META[currentApproverRole].label}.`,
    };
  }
  return { allowed: true };
}

/**
 * May this request climb another tier?
 *
 * False at the top: the Organization Admin has no one above them, so an
 * "escalate" there would be a button that quietly does nothing. A request that
 * reaches them is decided there or not at all — which is the PRD's own answer
 * for the request lane ("None — it climbs until a tier can grant it").
 *
 * NOTE this is the REQUEST ladder only. Agent sign-offs do not escalate this
 * way: §44.5 makes the security and release sign-offs unwaivable "by any role,
 * including the Organization Admin", so nothing here may be pointed at them.
 */
export function canEscalate(currentApproverRole: PlatformRole | null): boolean {
  return nextApproverRole(currentApproverRole) !== null;
}
