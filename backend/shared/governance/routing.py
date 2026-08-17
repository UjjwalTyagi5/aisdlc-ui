"""Where a REQUEST goes, and where it goes next.

REQUESTS ARE NOT APPROVALS. PRD §33.2 separates them on every axis, and this module
implements only the first column:

                 Request                        Approval
  Triggered by   a person needing something     an agent about to act
  Routes to      the first tier that can grant   the agent's OWNING role
  Direction      UPWARD: PA -> BU -> Org         SIDEWAYS, never to governance
  Fallback       none - it climbs                Project Admin, audited as such

Agent-gate routing is `shared/routers/approvals.py` and must stay there. Merging the
two would breach "approvals never route to a governance tier" and the no-approval-
laundering guardrail, which exist so a consequential action cannot be signed off by
someone who never had the standing to judge it.

MIRRORS `frontend/lib/requests/routing.ts` AND `frontend/lib/governance.ts`. The two
copies are load-bearing in different places — the frontend's drives the picker and the
"who will see this" preview, this one decides. They must agree, and
`tests/test_governance_routing.py` pins the parts that would silently diverge.

The frontend copy is a suggestion; this one is the rule. A picker that merely hides an
option is a hint, which is why `can_raise_type` is enforced at creation here rather
than trusted from the client.
"""
from __future__ import annotations

from typing import Literal, Optional

# ── the catalogue ────────────────────────────────────────────────────────────

REQUEST_TYPES: tuple[str, ...] = (
    "project_creation",
    "model_credential",
    "budget_increase",
    "project_archive",
    "agent_default_org",
    "agent_default_workspace",
    "agent_default_project",
    "connector_access",
    "mcp_server",
    "agent_access",
    "access_request",
    "user_onboarding",
    "role_assignment",
    "cross_bu_assignment",
    "model_provider_access",
    "other",
)

OPEN_STATUSES: tuple[str, ...] = ("draft", "submitted", "pending_review", "escalated")

# Human labels, mirroring REQUEST_TYPE_LABEL in the frontend schema. Used to
# compose a request's one-line `summary` server-side — the client sends a title
# and a description and does not get to write the summary, because it is the line
# the approver's queue is scanned by.
REQUEST_TYPE_LABEL: dict[str, str] = {
    "project_creation": "Project creation",
    "model_credential": "Model credential",
    "budget_increase": "Budget headroom",
    "project_archive": "Project archive",
    "agent_default_org": "Agent default — organization",
    "agent_default_workspace": "Agent default — business unit",
    "agent_default_project": "Agent default — project",
    "connector_access": "Connector access",
    "mcp_server": "MCP server",
    "agent_access": "Agent access",
    "access_request": "Access or permission",
    "user_onboarding": "User onboarding",
    "role_assignment": "Role assignment",
    "cross_bu_assignment": "Cross-unit contributor",
    "model_provider_access": "Model provider access",
    "other": "Other",
}

PRIORITIES: tuple[str, ...] = ("low", "normal", "high", "urgent")

# Phases, mirroring frontend lib/schemas/enums.ts::Phase.
PHASES: tuple[str, ...] = (
    "requirements",
    "design",
    "development",
    "review",
    "security",
    "testing",
    "deployment",
    "documentation",
    "discovery",
    "strategy",
    "migration_mapping",
    "validation",
    "data_engineering",
)

# Who owns each agent — the role that signs its gate off, and therefore the role
# that decides stage two of an agent-access request. Mirrors
# frontend/lib/roles.ts::AGENT_OWNER_ROLE, which encodes decisions the raw
# involvement table cannot express: Development is BUILT by the Developer and
# APPROVED by the Architect (never self-approval), and Documentation's owner is
# the Project Admin because acceptance there is automatic.
AGENT_OWNER_ROLE: dict[str, str] = {
    "requirements": "ba",
    "design": "architect",
    "development": "architect",
    "review": "architect",
    "security": "security_engineer",
    "testing": "qa",
    "deployment": "devops_engineer",
    "documentation": "project_admin",
    "discovery": "architect",
    "strategy": "architect",
    "migration_mapping": "architect",
    "validation": "qa",
    "data_engineering": "data_engineer",
}

# ── who decides what ─────────────────────────────────────────────────────────

# Mirrors frontend/lib/governance.ts::GOVERNANCE_APPROVER_ROLE. Exhaustive over
# REQUEST_TYPES on purpose: a new type cannot be added without a decision here.
# For the tier-routed types the entry is the FLOOR, used only when no requester
# role is known — `initial_approver_role` computes the real approver from who
# asked.
GOVERNANCE_APPROVER_ROLE: dict[str, str] = {
    "project_creation": "bu_admin",
    "model_credential": "bu_admin",
    # A BU Admin asking for more budget for their own unit needs the tier above
    # them, not a peer.
    "budget_increase": "org_admin",
    # A Project Admin (owner) or Org Admin archives directly; a BU Admin
    # archiving a project they do not own needs the Org Admin's sign-off.
    "project_archive": "org_admin",
    "agent_default_org": "org_admin",
    "agent_default_workspace": "bu_admin",
    "agent_default_project": "project_admin",
    # ── tier-routed floors ───────────────────────────────────────────────────
    "connector_access": "project_admin",
    "mcp_server": "project_admin",
    "access_request": "project_admin",
    "user_onboarding": "project_admin",
    "other": "project_admin",
    # The only type that routes DOWNWARD. The Org Admin raises it when placing a
    # Contributor in a unit, and the unit's admin is the only person who can say
    # what that person does there. Tier routing would look for someone above the
    # requester and find nobody.
    "role_assignment": "bu_admin",
    # Onboarding a provider is an organization-wide act whoever asks.
    "model_provider_access": "org_admin",
    # SIDEWAYS to a SPECIFIC Business Unit Admin — the one who owns the
    # contributor being borrowed, found via the request's workspace_id. Climbing
    # from the requester would land it with the BORROWING unit's admin, who
    # cannot lend somebody else's person.
    "cross_bu_assignment": "bu_admin",
    # Stage one of two. Stage two is the agent's own owner, which depends on the
    # phase and so cannot live in a map keyed by type.
    "agent_access": "project_admin",
}

# The types whose approver is fixed by the TYPE rather than by who asked. Every
# other type falls through to tier routing.
TYPE_ROUTED: frozenset[str] = frozenset(
    {
        "project_creation",
        "budget_increase",
        "project_archive",
        "agent_default_org",
        "agent_default_workspace",
        "agent_default_project",
        "model_provider_access",
        "agent_access",
        "role_assignment",
        "cross_bu_assignment",
        # `model_credential` is deliberately ABSENT. Its meaning is "make a model
        # available to my project", and who can grant that depends on who asks:
        # a contributor needs their Project Admin (who holds model:manage), a
        # Project Admin needs the unit's admin (because the model the unit was
        # never granted is above them). Fixed to bu_admin it always skipped the
        # Project Admin.
    }
)

# The ladder a request climbs, lowest rung first. Everything below
# `project_admin` — every delivery contributor — enters at the bottom, which is
# why the chain does not enumerate twelve roles: a BA and a Developer escalate
# identically, and listing each separately only creates twelve places to drift.
REQUEST_ESCALATION_CHAIN: tuple[str, ...] = ("project_admin", "bu_admin", "org_admin")

AgentAccessStage = Literal["project_admin", "agent_owner"]
AGENT_ACCESS_STAGES: tuple[str, ...] = ("project_admin", "agent_owner")


# ── what each tier may ask for ───────────────────────────────────────────────
#
# One rule generates all three lists: YOU REQUEST WHAT YOU CANNOT GRANT
# YOURSELF. The same noun therefore appears at two tiers meaning two different
# asks, which is deliberate rather than duplication:
#
#   model_credential       "grant this model to my project"    (below the BU)
#   model_provider_access  "onboard this provider org-wide"    (BU -> Org)

_CONTRIBUTOR_RAISABLE: tuple[str, ...] = (
    "agent_access",
    "access_request",
    "model_credential",
    "connector_access",
    "mcp_server",
    "user_onboarding",
    "other",
)

_PROJECT_ADMIN_RAISABLE: tuple[str, ...] = (
    "user_onboarding",
    "model_credential",
    "connector_access",
    "mcp_server",
    "budget_increase",
    "project_creation",
    "access_request",
    "other",
)

_BU_ADMIN_RAISABLE: tuple[str, ...] = (
    "model_provider_access",
    "user_onboarding",
    "connector_access",
    "budget_increase",
    "access_request",
    "other",
)

# Types the platform raises on someone's behalf rather than a person choosing
# them from the picker. They are absent from every *_RAISABLE list above — a
# role_assignment request is filed BY the onboarding flow, an agent_default_* by
# proposing a profile change, a project_archive by the archive action — so
# `can_raise_type` must not be the gate for them. The service names the ones it
# is allowed to file internally.
SYSTEM_RAISED: frozenset[str] = frozenset(
    {
        "role_assignment",
        "cross_bu_assignment",
        "project_archive",
        "agent_default_org",
        "agent_default_workspace",
        "agent_default_project",
    }
)


def raisable_types_for(role: Optional[str]) -> tuple[str, ...]:
    """The types this role may raise by hand, likeliest first, `other` last."""
    if role is None or role == "org_admin":
        return ()
    if role == "bu_admin":
        return _BU_ADMIN_RAISABLE
    if role == "project_admin":
        return _PROJECT_ADMIN_RAISABLE
    return _CONTRIBUTOR_RAISABLE


def can_raise_type(role: Optional[str], request_type: str) -> bool:
    return request_type in raisable_types_for(role)


def can_raise_request(role: Optional[str]) -> bool:
    """May this role raise a request at all?

    False for the Organization Admin alone, and not as a restriction so much as
    an arithmetic fact: the chain ends at them, so a request they raised would
    have nobody to decide it. They are the ceiling, so they only ever receive.
    """
    return role is not None and role != "org_admin"


def next_approver_role(current: Optional[str]) -> Optional[str]:
    """The next rung up, or None at the top of the ladder."""
    if current is None:
        return REQUEST_ESCALATION_CHAIN[0]
    if current not in REQUEST_ESCALATION_CHAIN:
        # Off the ladder = a delivery contributor. They enter at the bottom.
        return REQUEST_ESCALATION_CHAIN[0]
    i = REQUEST_ESCALATION_CHAIN.index(current)
    return REQUEST_ESCALATION_CHAIN[i + 1] if i + 1 < len(REQUEST_ESCALATION_CHAIN) else None


def initial_approver_role(request_type: str, requester_role: Optional[str]) -> Optional[str]:
    """Who decides this request first.

    Two rules, in order:
      1. A type-routed request goes where its type says.
      2. Anything else goes one tier above whoever raised it.

    Then one override on top of both: if that lands on the requester's OWN role,
    it climbs. "No one approves their own request — it escalates instead" is not
    a UI courtesy; routing has to enforce it, or a BU Admin filing a
    BU-Admin-routed request would silently become their own approver.
    """
    routed = (
        GOVERNANCE_APPROVER_ROLE.get(request_type)
        if request_type in TYPE_ROUTED
        else next_approver_role(requester_role)
    )

    # `cross_bu_assignment` is the exception, and the only one: its approver is a
    # DIFFERENT unit's admin — the one who owns the contributor being asked for,
    # identified by the request's workspace_id rather than by tier. Bumping it
    # sent a Business Unit Admin's ask to the Organization Admin, who has no
    # standing to lend another unit's people and every reason not to be asked.
    # Same rung, different unit, and the peer is not the requester.
    if request_type != "cross_bu_assignment" and routed is not None and routed == requester_role:
        return next_approver_role(routed)
    return routed


def escalation_ceiling_for(requester_role: Optional[str]) -> str:
    """How far a request may climb, given who raised it.

    A contributor's request stops at their Project Admin, who owns the project
    the ask is about — its members, its models, its tools — and either grants it
    or decides it is not worth pursuing. Letting it climb past them on its own
    would put an ask about one project in front of an Org Admin with no context,
    routing AROUND the person accountable rather than through them.

    That does not dead-end an ask the Project Admin cannot grant: when what they
    lack is a grant from above, they raise their OWN request for it, which climbs
    from their tier. The escalation is explicit and owned rather than automatic
    and anonymous.
    """
    if requester_role is None:
        return "org_admin"
    return "org_admin" if requester_role in REQUEST_ESCALATION_CHAIN else "project_admin"


def can_escalate(current_approver_role: Optional[str], requester_role: Optional[str]) -> bool:
    """May this request climb another tier?

    False at the top: the Organization Admin has nobody above them, so escalating
    there would be a no-op. False at the requester's ceiling for the reason above.
    """
    if current_approver_role == escalation_ceiling_for(requester_role):
        return False
    return next_approver_role(current_approver_role) is not None


# ── the two-stage type ───────────────────────────────────────────────────────


def agent_owner_role(phase: str) -> str:
    """The role that owns this agent, and so decides stage two."""
    return AGENT_OWNER_ROLE.get(phase, "project_admin")


def agent_access_approver(stage: str, phase: str) -> str:
    """The approver for one stage of an agent-access request.

    Kept beside `agent_owner_role` rather than inlined at call sites so the two
    stages cannot drift: the queue, the decide step and the "who sees this"
    preview all have to name the same person.
    """
    return "project_admin" if stage == "project_admin" else agent_owner_role(phase)


def next_agent_access_stage(stage: Optional[str], phase: str) -> Optional[str]:
    """The stage after this one, or None when the request is fully decided.

    Needs the phase, because whether a second stage EXISTS depends on it. Where
    the Project Admin is themselves the agent's owner — Documentation, whose
    acceptance is automatic — stage one already WAS the owner's decision, and
    advancing would hand the request back to the person who just made it. One
    approver, asked once.
    """
    if stage != "project_admin":
        return None
    return None if agent_owner_role(phase) == "project_admin" else "agent_owner"
