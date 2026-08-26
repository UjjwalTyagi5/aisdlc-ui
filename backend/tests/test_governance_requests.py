"""Governance requests: routing, the four rules, and what approving actually does.

Three layers, and each catches something the others cannot:

  routing     pure functions, no database. The tier ladder is a table of decisions
              and a table is best tested as one — these are the assertions that fail
              loudly when someone "simplifies" the same-role bump away.
  service     the rules as invoked, against a real tenant. Self-approval,
              wrong-approver, the escalation ceiling, the two-stage type.
  HTTP        the self-approval block with no UI in the picture. A rule that only
              holds when called the way the UI calls it is not a rule.
"""
import uuid as _uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

import process_api
from config.auth.jwt import create_access_token
from shared.db import get_db_session_for_tenant, get_db_session_superuser
from shared.governance import routing
from shared.services import governance_requests as svc
from shared.services.governance_requests import (
    AlreadyClosed,
    CannotEscalate,
    EffectUnavailable,
    GovernanceError,
    NotYourQueue,
    RequestNotFound,
    SelfApprovalBlocked,
)

pytestmark = pytest.mark.usefixtures("purge_created_orgs")


@pytest.fixture(autouse=True)
async def _dispose_shared_engine():
    yield
    from shared.db import engine
    await engine.dispose()


@pytest.fixture
async def org():
    """A throwaway tenant: one org, one unit, one project.

    Split across two sessions on purpose. `organizations` and `workspaces` are
    GLOBAL tables with no RLS, so the superuser session writes them. `projects` is
    FORCE RLS, and its WITH CHECK compares `tenant_id` against the
    `app.current_tenant_id` GUC — which only `get_db_session_for_tenant` sets.
    Inserting it on the superuser session fails the policy rather than bypassing it,
    which is the tenancy guarantee working, not a test problem to route around.
    """
    org_id, bu, project = str(_uuid.uuid4()), str(_uuid.uuid4()), str(_uuid.uuid4())
    async with get_db_session_superuser() as s:
        await s.execute(text(
            "INSERT INTO organizations (id, slug, display_name) VALUES (:i, :s, 'Gov Test')"
        ), {"i": org_id, "s": f"gov-{org_id[:8]}"})
        await s.execute(text(
            "INSERT INTO workspaces (id, organization_id, slug, display_name) "
            "VALUES (:i, :o, 'payments', 'Payments')"
        ), {"i": bu, "o": org_id})
    async with get_db_session_for_tenant(org_id) as s:
        await s.execute(text(
            "INSERT INTO projects (id, workspace_id, tenant_id, display_name, provider_kind) "
            "VALUES (:i, :w, :t, 'Core ledger', 'github')"
        ), {"i": project, "w": bu, "t": org_id})
    yield {"org": org_id, "bu": bu, "project": project}


async def _bind(org: dict, user_id: str, role: str, *, scope_kind: str, scope_id: str):
    """Give a user a real role binding, so effective_platform_role resolves them.

    The HTTP tests need this and the service tests do not: the service takes the
    role as an argument (it is the rule under test), while over HTTP the role is
    derived from bindings precisely so a caller cannot assert their own.
    """
    async with get_db_session_superuser() as s:
        await s.execute(text(
            "INSERT INTO users (id, email, password_hash, tenant_id, active) "
            "VALUES (:i, :e, 'x', :t, true) ON CONFLICT (id) DO NOTHING"
        ), {"i": user_id, "e": f"{user_id}@abcbank.com", "t": org["org"]})
    async with get_db_session_for_tenant(org["org"]) as s:
        await s.execute(text(
            "INSERT INTO role_bindings (id, user_id, scope_kind, scope_id, role_name, tenant_id) "
            "VALUES (CAST(:i AS uuid), :u, :sk, CAST(:si AS uuid), :r, CAST(:t AS uuid))"
        ), {
            "i": str(_uuid.uuid4()), "u": user_id, "sk": scope_kind,
            "si": scope_id, "r": role, "t": org["org"],
        })


async def _raise(org, *, initiator, role, rtype="access_request", **kw):
    async with get_db_session_for_tenant(org["org"]) as s:
        return await svc.create_request(
            s,
            tenant_id=org["org"],
            initiator_id=initiator,
            initiator_name=initiator.split("-")[0].title(),
            initiator_role=role,
            request_type=rtype,
            title=kw.pop("title", "Need access to the deploy pipeline"),
            description=kw.pop("description", "I cannot ship without it."),
            workspace_id=org["bu"],
            **kw,
        )


# ── routing: the decision table ──────────────────────────────────────────────

def test_a_request_climbs_one_tier_from_whoever_raised_it():
    assert routing.initial_approver_role("access_request", "developer") == "project_admin"
    assert routing.initial_approver_role("access_request", "project_admin") == "bu_admin"
    assert routing.initial_approver_role("access_request", "bu_admin") == "org_admin"


def test_a_type_routed_request_ignores_who_raised_it():
    # A project creation goes to the BU Admin no matter who filed it.
    assert routing.initial_approver_role("project_creation", "developer") == "bu_admin"
    assert routing.initial_approver_role("project_creation", "project_admin") == "bu_admin"


def test_nobody_decides_their_own_tiers_ask():
    """The bump. A BU Admin's own ask must not land back with a BU Admin."""
    assert routing.initial_approver_role("project_creation", "bu_admin") == "org_admin"
    # budget_increase is tier-routed now, so it proves the same thing directly:
    # a BU Admin asking for their unit's headroom climbs past their own tier.
    assert routing.initial_approver_role("budget_increase", "bu_admin") == "org_admin"


def test_a_project_admins_budget_ask_stops_at_their_bu_admin():
    """Headroom for one project is the unit admin's call, not the Org Admin's.

    Pinned to org_admin (it used to be TYPE_ROUTED), a Project Admin whose project
    had spent its total budget had to reach the top of the organisation to get it
    raised, past the person who owns the unit's cap.
    """
    assert routing.initial_approver_role("budget_increase", "project_admin") == "bu_admin"
    assert routing.initial_approver_role("budget_increase", "developer") == "project_admin"


def test_cross_bu_assignment_is_not_bumped():
    """The one exception, and it is deliberate.

    Its approver is a DIFFERENT unit's admin — the one who owns the contributor
    being borrowed — identified by the request's workspace, not by tier. Bumping it
    would send a BU Admin's ask to the Org Admin, who has no standing to lend
    another unit's people.
    """
    assert routing.initial_approver_role("cross_bu_assignment", "bu_admin") == "bu_admin"


def test_role_assignment_routes_downward():
    """The one type that goes DOWN. The Org Admin raises it; the unit's admin answers.

    Tier routing would look for somebody above the requester and find nobody,
    because the requester is already at the top.
    """
    assert routing.initial_approver_role("role_assignment", "org_admin") == "bu_admin"


def test_model_credential_is_tier_routed_not_type_routed():
    """Who can grant "make this model available to my project" depends on who asks."""
    assert routing.initial_approver_role("model_credential", "developer") == "project_admin"
    assert routing.initial_approver_role("model_credential", "project_admin") == "bu_admin"


def test_a_contributors_request_stops_at_their_project_admin():
    assert routing.escalation_ceiling_for("developer") == "project_admin"
    assert routing.can_escalate("project_admin", "developer") is False
    # An admin tier keeps the full ladder — their asks are genuinely about the tier
    # above them, so there is nobody below to route through.
    assert routing.escalation_ceiling_for("project_admin") == "org_admin"
    assert routing.can_escalate("project_admin", "project_admin") is True


def test_the_org_admin_is_the_ceiling():
    assert routing.can_raise_request("org_admin") is False
    assert routing.can_escalate("org_admin", "bu_admin") is False


def test_each_tier_asks_for_what_it_cannot_grant_itself():
    # A Developer cannot ask for a project or a budget — neither is theirs at that level.
    assert routing.can_raise_type("developer", "project_creation") is False
    assert routing.can_raise_type("developer", "budget_increase") is False
    assert routing.can_raise_type("developer", "agent_access") is True
    # A BU Admin wants an org-wide PROVIDER, not a project-scoped credential.
    assert routing.can_raise_type("bu_admin", "model_provider_access") is True
    assert routing.can_raise_type("bu_admin", "model_credential") is False


def test_agent_access_second_stage_is_the_agents_owner():
    assert routing.agent_access_approver("project_admin", "security") == "project_admin"
    assert routing.agent_access_approver("agent_owner", "security") == "security_engineer"
    assert routing.next_agent_access_stage("project_admin", "security") == "agent_owner"


def test_agent_access_has_one_stage_where_the_project_admin_owns_the_agent():
    """Documentation's owner IS the Project Admin — acceptance there is automatic.

    Advancing would hand the request back to the person who just decided it.
    """
    assert routing.agent_owner_role("documentation") == "project_admin"
    assert routing.next_agent_access_stage("project_admin", "documentation") is None


def test_every_type_has_an_approver_floor():
    """Exhaustive by construction, so a new type cannot be added without a decision."""
    assert set(routing.GOVERNANCE_APPROVER_ROLE) == set(routing.REQUEST_TYPES)
    assert set(routing.REQUEST_TYPE_LABEL) == set(routing.REQUEST_TYPES)


# ── the rules, in the service ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_initiator_cannot_decide_their_own_request(org):
    alice = f"alice-{_uuid.uuid4()}"
    req = await _raise(org, initiator=alice, role="developer")
    assert req["currentApproverRole"] == "project_admin"

    async with get_db_session_for_tenant(org["org"]) as s:
        with pytest.raises(SelfApprovalBlocked) as ei:
            await svc.decide(
                s, request_id=req["id"], decider_id=alice, decider_name="Alice",
                decider_role="project_admin", decision="approve",
            )
    assert ei.value.code == "SELF_APPROVAL_BLOCKED"
    assert ei.value.http_status == 400

    # Rejecting your own is the same act with the opposite sign.
    async with get_db_session_for_tenant(org["org"]) as s:
        with pytest.raises(SelfApprovalBlocked):
            await svc.decide(
                s, request_id=req["id"], decider_id=alice, decider_name="Alice",
                decider_role="project_admin", decision="reject",
            )
        assert (await svc.get_request(s, req["id"]))["status"] == "submitted"


@pytest.mark.asyncio
async def test_only_the_current_approver_decides(org):
    alice, bob = f"alice-{_uuid.uuid4()}", f"bob-{_uuid.uuid4()}"
    req = await _raise(org, initiator=alice, role="developer")

    # Bob holds a governance role, but this request is waiting on a Project Admin.
    async with get_db_session_for_tenant(org["org"]) as s:
        with pytest.raises(NotYourQueue):
            await svc.decide(
                s, request_id=req["id"], decider_id=bob, decider_name="Bob",
                decider_role="bu_admin", decision="approve",
            )

    async with get_db_session_for_tenant(org["org"]) as s:
        out = await svc.decide(
            s, request_id=req["id"], decider_id=bob, decider_name="Bob",
            decider_role="project_admin", decision="approve", reason="fair enough",
        )
    assert out["status"] == "approved"
    assert out["decidedBy"] == "Bob"
    assert out["decidedAt"] is not None
    # Closed means waiting on nobody — enforced by a CHECK constraint too.
    assert out["currentApproverRole"] is None


@pytest.mark.asyncio
async def test_role_assignment_closes_when_the_role_is_actually_assigned(org):
    """The onboarding.py contract: a role_assignment request closes because the
    membership changed, not because someone clicked approve on it — decide()
    cannot apply it (no role is named in the request itself), so
    complete_role_assignment is the only path that ever closes one.

    Regression test for the exact bug reported live: a BU Admin used the "Assign
    role" dialog (which calls update_workspace_member_role, not decide()), the
    Users page showed the new role immediately, and the request sat in the
    inbox forever because nothing ever closed it — complete_role_assignment did
    not exist yet.
    """
    ana = f"ana-{_uuid.uuid4()}"
    req = await _raise(
        org, initiator=f"orgadmin-{_uuid.uuid4()}", role="org_admin",
        rtype="role_assignment", system_raised=True, target_ref=ana,
        payload={"userId": ana, "email": f"{ana}@abcbank.com"},
        title=f"Role needed: Ana in {org['bu']}",
    )
    assert req["status"] == "submitted"
    assert req["currentApproverRole"] == "bu_admin"

    async with get_db_session_for_tenant(org["org"]) as s:
        await svc.complete_role_assignment(
            s, tenant_id=org["org"], workspace_id=org["bu"], user_id=ana,
            role_label="Project Admin", decided_by_id="farah-1", decided_by_name="Farah",
        )

    async with get_db_session_for_tenant(org["org"]) as s:
        out = await svc.get_request(s, req["id"])
    assert out["status"] == "approved"
    assert out["decidedBy"] == "Farah"
    assert out["decidedAt"] is not None
    assert out["currentApproverRole"] is None
    assert out["reason"] == "Assigned Project Admin."
    assert out["timeline"][-1]["kind"] == "approved"
    assert out["timeline"][-1]["note"] == "Assigned Project Admin."


@pytest.mark.asyncio
async def test_role_assignment_close_is_a_noop_with_no_open_request(org):
    """A role changed for some other reason (correction, re-assignment) must not
    invent a request to close — same contract the frontend mock documents."""
    priya = f"priya-{_uuid.uuid4()}"
    async with get_db_session_for_tenant(org["org"]) as s:
        # Must not raise even though no role_assignment request exists at all.
        await svc.complete_role_assignment(
            s, tenant_id=org["org"], workspace_id=org["bu"], user_id=priya,
            role_label="Developer", decided_by_id="farah-1", decided_by_name="Farah",
        )


@pytest.mark.asyncio
async def test_self_approval_is_reported_ahead_of_already_closed(org):
    """The more specific answer wins: 'you raised this' beats 'this is closed'."""
    alice, bob = f"alice-{_uuid.uuid4()}", f"bob-{_uuid.uuid4()}"
    req = await _raise(org, initiator=alice, role="developer")
    async with get_db_session_for_tenant(org["org"]) as s:
        await svc.decide(
            s, request_id=req["id"], decider_id=bob, decider_name="Bob",
            decider_role="project_admin", decision="approve",
        )
    async with get_db_session_for_tenant(org["org"]) as s:
        with pytest.raises(SelfApprovalBlocked):
            await svc.decide(
                s, request_id=req["id"], decider_id=alice, decider_name="Alice",
                decider_role="project_admin", decision="approve",
            )


@pytest.mark.asyncio
async def test_a_closed_request_cannot_be_decided_again(org):
    alice, bob, carol = (f"{n}-{_uuid.uuid4()}" for n in ("alice", "bob", "carol"))
    req = await _raise(org, initiator=alice, role="developer")
    async with get_db_session_for_tenant(org["org"]) as s:
        await svc.decide(
            s, request_id=req["id"], decider_id=bob, decider_name="Bob",
            decider_role="project_admin", decision="approve",
        )
    async with get_db_session_for_tenant(org["org"]) as s:
        with pytest.raises(AlreadyClosed):
            await svc.decide(
                s, request_id=req["id"], decider_id=carol, decider_name="Carol",
                decider_role="project_admin", decision="reject",
            )


@pytest.mark.asyncio
async def test_an_org_admin_cannot_raise_a_request(org):
    async with get_db_session_for_tenant(org["org"]) as s:
        with pytest.raises(GovernanceError) as ei:
            await svc.create_request(
                s, tenant_id=org["org"], initiator_id="ozzy", initiator_name="Ozzy",
                initiator_role="org_admin", request_type="access_request",
                title="Something", description="Because I want it",
                workspace_id=org["bu"],
            )
    assert ei.value.code == "CANNOT_RAISE"


@pytest.mark.asyncio
async def test_a_type_the_tier_may_not_raise_is_refused(org):
    """The picker hides it; this is what makes hiding it more than a suggestion."""
    async with get_db_session_for_tenant(org["org"]) as s:
        with pytest.raises(GovernanceError) as ei:
            await svc.create_request(
                s, tenant_id=org["org"], initiator_id="dev", initiator_name="Dev",
                initiator_role="developer", request_type="budget_increase",
                title="More money please", description="The cap is binding",
                workspace_id=org["bu"],
            )
    assert ei.value.code == "TYPE_NOT_RAISABLE"


@pytest.mark.asyncio
async def test_a_system_raised_type_bypasses_the_raisable_check_but_only_when_asked(org):
    """role_assignment is filed BY the onboarding flow, so no tier lists it.

    The bypass has to be requested explicitly — inferring it would make the
    raisable check bypassable from outside.
    """
    async with get_db_session_for_tenant(org["org"]) as s:
        out = await svc.create_request(
            s, tenant_id=org["org"], initiator_id="ozzy", initiator_name="Ozzy",
            initiator_role="org_admin", request_type="role_assignment",
            title="Give Amara a role", description="Placed in Payments, needs a role",
            workspace_id=org["bu"], system_raised=True,
        )
    assert out["currentApproverRole"] == "bu_admin"

    async with get_db_session_for_tenant(org["org"]) as s:
        with pytest.raises(GovernanceError) as ei:
            await svc.create_request(
                s, tenant_id=org["org"], initiator_id="dev", initiator_name="Dev",
                initiator_role="developer", request_type="access_request",
                title="Sneak past the check", description="Should not be allowed",
                workspace_id=org["bu"], system_raised=True,
            )
    assert ei.value.code == "NOT_SYSTEM_RAISED"


# ── climbing ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_request_climbs_and_records_where_it_went(org):
    alice = f"alice-{_uuid.uuid4()}"
    req = await _raise(org, initiator=alice, role="project_admin")
    assert req["currentApproverRole"] == "bu_admin"

    async with get_db_session_for_tenant(org["org"]) as s:
        out = await svc.escalate(
            s, request_id=req["id"], actor_id=alice, actor_name="Alice",
            actor_role="project_admin", note="No answer for a week",
        )
    assert out["status"] == "escalated"
    assert out["currentApproverRole"] == "org_admin"
    assert out["escalationCount"] == 1
    # The trail names the role it moved TO — which is what makes "why is an Org
    # Admin deciding this" answerable from the request itself.
    escalated = [e for e in out["timeline"] if e["kind"] == "escalated"]
    assert escalated and escalated[-1]["toRole"] == "org_admin"


@pytest.mark.asyncio
async def test_a_contributors_request_will_not_climb_past_their_project_admin(org):
    alice = f"alice-{_uuid.uuid4()}"
    req = await _raise(org, initiator=alice, role="developer")
    async with get_db_session_for_tenant(org["org"]) as s:
        with pytest.raises(CannotEscalate):
            await svc.escalate(
                s, request_id=req["id"], actor_id=alice, actor_name="Alice",
                actor_role="developer",
            )


@pytest.mark.asyncio
async def test_only_the_initiator_withdraws(org):
    alice, bob = f"alice-{_uuid.uuid4()}", f"bob-{_uuid.uuid4()}"
    req = await _raise(org, initiator=alice, role="developer")

    async with get_db_session_for_tenant(org["org"]) as s:
        with pytest.raises(NotYourQueue):
            await svc.cancel(s, request_id=req["id"], actor_id=bob, actor_name="Bob")

    async with get_db_session_for_tenant(org["org"]) as s:
        out = await svc.cancel(
            s, request_id=req["id"], actor_id=alice, actor_name="Alice",
            reason="Sorted it myself",
        )
    assert out["status"] == "cancelled"
    assert out["currentApproverRole"] is None
    # Cancelled is a withdrawal, not a decision — so it has no decider.
    assert out["decidedBy"] is None


# ── the two-stage type ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_agent_access_is_answered_twice(org):
    alice, pa, sec = (f"{n}-{_uuid.uuid4()}" for n in ("alice", "pa", "sec"))
    req = await _raise(
        org, initiator=alice, role="developer", rtype="agent_access", phase="security",
        title="Need the security agent", description="To clear the SCA exemption",
    )
    assert req["approvalStage"] == "project_admin"
    assert req["currentApproverRole"] == "project_admin"

    # Stage one: the Project Admin says this person should be doing this work.
    async with get_db_session_for_tenant(org["org"]) as s:
        mid = await svc.decide(
            s, request_id=req["id"], decider_id=pa, decider_name="Pa",
            decider_role="project_admin", decision="approve",
        )
    # Not decided — moved on. The distinction is the point: an escalation means the
    # first approver did not answer; here they did.
    assert mid["status"] == "pending_review"
    assert mid["approvalStage"] == "agent_owner"
    assert mid["currentApproverRole"] == "security_engineer"
    assert mid["escalationCount"] == 0
    assert mid["decidedBy"] is None

    # Stage two: the agent's owner decides whether the agent should do it for them.
    async with get_db_session_for_tenant(org["org"]) as s:
        final = await svc.decide(
            s, request_id=req["id"], decider_id=sec, decider_name="Sec",
            decider_role="security_engineer", decision="approve",
        )
    assert final["status"] == "approved"
    assert final["currentApproverRole"] is None


@pytest.mark.asyncio
async def test_a_stage_one_rejection_ends_it(org):
    """A no at stage one saves the agent's owner a decision entirely."""
    alice, pa = f"alice-{_uuid.uuid4()}", f"pa-{_uuid.uuid4()}"
    req = await _raise(
        org, initiator=alice, role="developer", rtype="agent_access", phase="security",
        title="Need the security agent", description="To clear the SCA exemption",
    )
    async with get_db_session_for_tenant(org["org"]) as s:
        out = await svc.decide(
            s, request_id=req["id"], decider_id=pa, decider_name="Pa",
            decider_role="project_admin", decision="reject", reason="Not your work",
        )
    assert out["status"] == "rejected"
    assert out["currentApproverRole"] is None


# ── approving has to DO something ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_approving_a_budget_increase_moves_the_cap(org):
    pa, ozzy = f"pa-{_uuid.uuid4()}", f"ozzy-{_uuid.uuid4()}"
    async with get_db_session_for_tenant(org["org"]) as s:
        req = await svc.create_request(
            s, tenant_id=org["org"], initiator_id=pa, initiator_name="Pa",
            initiator_role="project_admin", request_type="budget_increase",
            title="Payments needs $16,000", description="96% of cap with nine days left",
            workspace_id=org["bu"], payload={"requestedAmountUsd": 16000},
        )
    assert req["currentApproverRole"] == "bu_admin"

    async with get_db_session_for_tenant(org["org"]) as s:
        out = await svc.decide(
            s, request_id=req["id"], decider_id=ozzy, decider_name="Ozzy",
            decider_role="bu_admin", decision="approve",
        )
    assert out["status"] == "approved"

    async with get_db_session_superuser() as s:
        cap = (await s.execute(
            text("SELECT monthly_budget_usd FROM workspaces WHERE id = :w"), {"w": org["bu"]}
        )).scalar()
    assert float(cap) == 16000.0


@pytest.mark.asyncio
async def test_rejecting_a_budget_increase_leaves_the_cap_alone(org):
    pa, ozzy = f"pa-{_uuid.uuid4()}", f"ozzy-{_uuid.uuid4()}"
    async with get_db_session_for_tenant(org["org"]) as s:
        req = await svc.create_request(
            s, tenant_id=org["org"], initiator_id=pa, initiator_name="Pa",
            initiator_role="project_admin", request_type="budget_increase",
            title="Payments needs $16,000", description="96% of cap with nine days left",
            workspace_id=org["bu"], payload={"requestedAmountUsd": 16000},
        )
    async with get_db_session_for_tenant(org["org"]) as s:
        await svc.decide(
            s, request_id=req["id"], decider_id=ozzy, decider_name="Ozzy",
            decider_role="bu_admin", decision="reject", reason="Not this quarter",
        )
    async with get_db_session_superuser() as s:
        cap = (await s.execute(
            text("SELECT monthly_budget_usd FROM workspaces WHERE id = :w"), {"w": org["bu"]}
        )).scalar()
    assert cap is None


@pytest.mark.asyncio
async def test_approving_an_archive_request_archives_the_project(org):
    bu_admin, ozzy = f"bu-{_uuid.uuid4()}", f"ozzy-{_uuid.uuid4()}"
    async with get_db_session_for_tenant(org["org"]) as s:
        req = await svc.create_request(
            s, tenant_id=org["org"], initiator_id=bu_admin, initiator_name="Bua",
            initiator_role="bu_admin", request_type="project_archive",
            title="Archive Core ledger", description="Delivery finished last month",
            workspace_id=org["bu"], project_id=org["project"], target_ref=org["project"],
            system_raised=True,
        )
    async with get_db_session_for_tenant(org["org"]) as s:
        await svc.decide(
            s, request_id=req["id"], decider_id=ozzy, decider_name="Ozzy",
            decider_role="org_admin", decision="approve",
        )
    async with get_db_session_for_tenant(org["org"]) as s:
        archived = (await s.execute(
            text("SELECT archived FROM projects WHERE id = CAST(:p AS uuid)"),
            {"p": org["project"]},
        )).scalar()
    assert archived is True


@pytest.mark.asyncio
async def test_an_approval_that_cannot_take_effect_is_refused_not_recorded(org):
    """A budget request with no amount has nothing to apply, so it is not approved.

    Recording it would leave the request looking settled while nothing changed, and
    the person who approved it has no reason to go and check.
    """
    pa, ozzy = f"pa-{_uuid.uuid4()}", f"ozzy-{_uuid.uuid4()}"
    async with get_db_session_for_tenant(org["org"]) as s:
        req = await svc.create_request(
            s, tenant_id=org["org"], initiator_id=pa, initiator_name="Pa",
            initiator_role="project_admin", request_type="budget_increase",
            title="More headroom please", description="No figure attached",
            workspace_id=org["bu"],
        )
    async with get_db_session_for_tenant(org["org"]) as s:
        with pytest.raises(EffectUnavailable):
            await svc.decide(
                s, request_id=req["id"], decider_id=ozzy, decider_name="Ozzy",
                decider_role="bu_admin", decision="approve",
            )
    async with get_db_session_for_tenant(org["org"]) as s:
        assert (await svc.get_request(s, req["id"]))["status"] == "submitted"


@pytest.mark.asyncio
async def test_an_unknown_request_is_not_found(org):
    async with get_db_session_for_tenant(org["org"]) as s:
        with pytest.raises(RequestNotFound):
            await svc.get_request(s, str(_uuid.uuid4()))
        with pytest.raises(RequestNotFound):
            await svc.get_request(s, "not-a-uuid")


# ── reading ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_you_always_see_what_you_raised_even_bound_to_nothing(org):
    """An initiator must not lose sight of their request when it climbs out of scope.

    That is precisely when they most want to know where it went.
    """
    alice = f"alice-{_uuid.uuid4()}"
    await _raise(org, initiator=alice, role="developer")

    async with get_db_session_for_tenant(org["org"]) as s:
        mine = await svc.list_requests(s, viewer_id=alice, allowed_workspace_ids=[])
        theirs = await svc.list_requests(s, viewer_id="somebody-else", allowed_workspace_ids=[])
    assert len(mine) == 1
    assert theirs == []


@pytest.mark.asyncio
async def test_org_wide_sees_the_whole_queue(org):
    """None means org-wide; an EMPTY LIST is a real answer and must not be conflated."""
    alice = f"alice-{_uuid.uuid4()}"
    await _raise(org, initiator=alice, role="developer")
    async with get_db_session_for_tenant(org["org"]) as s:
        assert len(await svc.list_requests(s, viewer_id="ozzy", allowed_workspace_ids=None)) == 1


@pytest.mark.asyncio
async def test_the_timeline_records_creation_and_routing(org):
    alice = f"alice-{_uuid.uuid4()}"
    req = await _raise(org, initiator=alice, role="developer")
    kinds = [e["kind"] for e in req["timeline"]]
    assert kinds == ["created", "assigned"]
    assert req["timeline"][1]["toRole"] == "project_admin"


# ── over real HTTP, with no UI ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_deciding_needs_the_governance_permission(org):
    """The lane was authorised by ROLE-STRING MATCHING alone.

    `decider_role == currentApproverRole` answers whose turn it is; nothing answered
    whether the role takes governance decisions at all. A tenant-defined custom role
    could therefore be neither granted nor denied one — the exact thing custom roles
    exist to express.

    Here the caller IS the right approver (bound project_admin, and the request is
    waiting on project_admin) and is still refused, because the permission is what
    they lack.
    """
    alice = f"alice-{_uuid.uuid4()}"
    bob = f"bob-{_uuid.uuid4()}"
    await _bind(org, bob, "project_admin", scope_kind="project", scope_id=org["project"])
    req = await _raise(org, initiator=alice, role="developer")
    assert req["currentApproverRole"] == "project_admin"

    c = TestClient(process_api.app)
    headers = {"Authorization": "Bearer " + create_access_token(
        user_id=bob, tenant_id=org["org"], permissions=["artifact:view"],
    )}
    r = c.post(f"/governance-approvals/{req['id']}/decide", headers=headers,
               json={"decision": "approve"})
    assert r.status_code == 403, r.text

    # With it, the same caller on the same request goes through — proving the refusal
    # above was the permission and not the routing.
    ok = {"Authorization": "Bearer " + create_access_token(
        user_id=bob, tenant_id=org["org"],
        permissions=["artifact:view", "governance:decide"],
    )}
    r2 = c.post(f"/governance-approvals/{req['id']}/decide", headers=ok,
                json={"decision": "approve"})
    assert r2.status_code == 200, r2.text


@pytest.mark.asyncio
async def test_withdrawing_your_own_request_needs_no_permission(org):
    """Cancel is the INITIATOR's act, and initiators are usually delivery roles.

    Gating it on `governance:decide` would take away the ability to withdraw your own
    request — which is why only /decide carries the permission, and /cancel and
    /escalate deliberately do not.
    """
    alice = f"alice-{_uuid.uuid4()}"
    req = await _raise(org, initiator=alice, role="developer")

    c = TestClient(process_api.app)
    headers = {"Authorization": "Bearer " + create_access_token(
        user_id=alice, tenant_id=org["org"], permissions=["artifact:view"],
    )}
    r = c.post(f"/governance-approvals/{req['id']}/cancel", headers=headers,
               json={"reason": "sorted it myself"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "cancelled"


@pytest.mark.asyncio
async def test_self_approval_blocked_over_http(org):
    alice = f"alice-{_uuid.uuid4()}"
    # A project_admin binding, so effective_platform_role puts the caller on a real
    # rung. Without it they are refused as the wrong approver and the self-approval
    # rule is never reached — which would make this test pass for the wrong reason.
    await _bind(org, alice, "project_admin", scope_kind="project", scope_id=org["project"])

    c = TestClient(process_api.app)
    headers = {"Authorization": "Bearer " + create_access_token(
        user_id=alice, tenant_id=org["org"],
        # A real project_admin's set for this lane: `governance:decide` says the role
        # takes governance decisions at all. Without it the route refuses at the
        # permission gate and the self-approval rule below is never reached — which
        # would make this test pass for the wrong reason, exactly as the binding above
        # guards against.
        permissions=["artifact:view", "governance:decide"],
    )}

    created = c.post("/governance-approvals", headers=headers, json={
        "type": "budget_increase",
        "title": "Payments needs more headroom",
        "description": "We are at 96% of the cap with nine days to go.",
        "workspaceId": org["bu"],
    })
    assert created.status_code == 201, created.text
    body = created.json()
    # The initiator and their role come from the session, so neither can be spoofed.
    assert body["requestedById"] == alice
    assert body["requestedByRole"] == "project_admin"
    assert body["currentApproverRole"] == "bu_admin"

    r = c.post(f"/governance-approvals/{body['id']}/decide", headers=headers,
               json={"decision": "approve"})
    # Refused as self-approval, not as the wrong queue — the more actionable answer.
    assert r.status_code == 400, r.text
    assert r.json()["detail"]["code"] == "SELF_APPROVAL_BLOCKED", r.json()


@pytest.mark.asyncio
async def test_raisable_types_matches_what_create_will_accept(org):
    """The picker is built from this, so it can never offer an option create refuses."""
    dev = f"dev-{_uuid.uuid4()}"
    await _bind(org, dev, "developer", scope_kind="project", scope_id=org["project"])

    c = TestClient(process_api.app)
    headers = {"Authorization": "Bearer " + create_access_token(
        user_id=dev, tenant_id=org["org"], permissions=["artifact:view"]
    )}
    r = c.get("/governance-approvals/raisable-types", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["role"] == "developer"
    assert r.json()["canRaise"] is True
    assert "budget_increase" not in r.json()["types"]
    assert "agent_access" in r.json()["types"]
