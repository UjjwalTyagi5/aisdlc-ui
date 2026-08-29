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
async def test_approval_flips_status_before_applying_the_effect(org, monkeypatch):
    """The guarded status UPDATE (the one `AlreadyClosed` above depends on) must run
    BEFORE `apply_on_approve`, not after.

    Most effects write through the same `db` session `decide()` already holds, so
    statement order within that one transaction cannot change what they persist —
    nothing commits until the session's own final commit regardless. But
    `_apply_model_credential` (backend/shared/governance/effects.py) calls
    `set_project_selection`/`get_project_selection`, which open their OWN session
    via `get_db_session_for_tenant` and commit independently, right away. If that
    effect ran BEFORE the guarded UPDATE, a request closed by a second, concurrent
    `decide()` call could still let this call's model-selection write through even
    though this call's own status flip loses the race and raises AlreadyClosed —
    an "effect applied, request never marked approved" half-succeed.

    Proven here without needing genuine concurrency: `apply_on_approve` is spied on
    to read `governance_requests.status` for this request, in the SAME (still
    uncommitted) session, right as it is called. That read can only see 'approved'
    if the guarded UPDATE already ran in this transaction — which is exactly the
    ordering this test locks in."""
    alice, bob = f"alice-{_uuid.uuid4()}", f"bob-{_uuid.uuid4()}"
    req = await _raise(org, initiator=alice, role="developer")

    from shared.services import governance_requests as svc_module

    seen_status_when_effect_ran: list[str | None] = []
    real_apply_on_approve = svc_module.apply_on_approve

    async def spy_apply_on_approve(db, request):
        row = (
            await db.execute(
                text("SELECT status FROM governance_requests WHERE id = CAST(:i AS uuid)"),
                {"i": request["id"]},
            )
        ).first()
        seen_status_when_effect_ran.append(row.status if row else None)
        return await real_apply_on_approve(db, request)

    monkeypatch.setattr(svc_module, "apply_on_approve", spy_apply_on_approve)

    async with get_db_session_for_tenant(org["org"]) as s:
        out = await svc.decide(
            s, request_id=req["id"], decider_id=bob, decider_name="Bob",
            decider_role="project_admin", decision="approve",
        )
    assert out["status"] == "approved"
    assert seen_status_when_effect_ran == ["approved"]


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
async def test_approving_a_settings_change_with_a_dead_description_field_does_not_crash(org):
    """`description` is not a real `projects` column (live baseline audit,
    2026-08-29): approving a queued settings change whose payload included it used
    to crash with a raw 500 `UndefinedColumnError` on `UPDATE projects SET
    description = ...`, leaving the request stuck open forever (the crash rolled
    back the status flip too). It must now be silently ignored, same as any other
    field the project no longer has — the real field alongside it still applies.
    """
    pa, bua = f"pa-{_uuid.uuid4()}", f"bua-{_uuid.uuid4()}"
    async with get_db_session_for_tenant(org["org"]) as s:
        req = await svc.create_request(
            s, tenant_id=org["org"], initiator_id=pa, initiator_name="Pa",
            initiator_role="project_admin", request_type="project_settings_change",
            title="Settings change for Core ledger",
            description="Requested changes to: name, description.",
            workspace_id=org["bu"], project_id=org["project"], target_ref=org["project"],
            payload={"changes": {"name": "Renamed Ledger", "description": "New blurb"}},
            system_raised=True,
        )
    async with get_db_session_for_tenant(org["org"]) as s:
        await svc.decide(
            s, request_id=req["id"], decider_id=bua, decider_name="Bua",
            decider_role="bu_admin", decision="approve",
        )
    async with get_db_session_for_tenant(org["org"]) as s:
        name = (await s.execute(
            text("SELECT display_name FROM projects WHERE id = CAST(:p AS uuid)"),
            {"p": org["project"]},
        )).scalar()
    assert name == "Renamed Ledger"


@pytest.mark.asyncio
async def test_an_approval_that_cannot_take_effect_is_refused_not_recorded(org):
    """A budget request with no amount has nothing to apply, so it is not approved.

    Recording it would leave the request looking settled while nothing changed, and
    the person who approved it has no reason to go and check.

    `pytest.raises` wraps the WHOLE `async with get_db_session_for_tenant(...)`
    block here, deliberately — not just the `decide()` call. `decide()`'s guarded
    status UPDATE now runs before `apply_on_approve` (see the ordering comment in
    governance_requests.py), so by the time the effect raises EffectUnavailable for
    this budget request, the UPDATE has already executed, uncommitted, in this same
    transaction. The only thing that undoes it is the session's own except-rollback
    in `get_db_session_for_tenant` — which fires only if the exception actually
    propagates OUT of the `async with` block, exactly as it does through the real
    FastAPI route (`shared/routers/governance_requests.py`'s `except GovernanceError
    as exc: raise _http(exc)` re-raises into the `Depends(get_db_session)` generator).
    Catching the exception INSIDE the `async with` (the previous shape of this test)
    would let `pytest.raises` swallow it before the session ever sees a failure,
    so the block would exit normally and COMMIT the status flip the effect never
    approved — silently defeating the exact guarantee this test's docstring claims.
    """
    pa, ozzy = f"pa-{_uuid.uuid4()}", f"ozzy-{_uuid.uuid4()}"
    async with get_db_session_for_tenant(org["org"]) as s:
        req = await svc.create_request(
            s, tenant_id=org["org"], initiator_id=pa, initiator_name="Pa",
            initiator_role="project_admin", request_type="budget_increase",
            title="More headroom please", description="No figure attached",
            workspace_id=org["bu"],
        )
    with pytest.raises(EffectUnavailable):
        async with get_db_session_for_tenant(org["org"]) as s:
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


@pytest.mark.asyncio
async def test_connector_access_request_carries_target_id(org):
    """A client-raised connector_access request must record WHICH connector
    it's about — without this, _apply_connector_access can never find a
    target to grant (the bug this plan exists to fix)."""
    dev = f"dev-{_uuid.uuid4()}"
    await _bind(org, dev, "developer", scope_kind="project", scope_id=org["project"])

    c = TestClient(process_api.app)
    headers = {"Authorization": "Bearer " + create_access_token(
        user_id=dev, tenant_id=org["org"], permissions=["artifact:view", "run:create"],
    )}
    r = c.post(
        "/governance-approvals", headers=headers,
        json={
            "type": "connector_access", "title": "Slack access",
            "description": "Need it for the release channel.", "priority": "normal",
            "workspaceId": org["bu"], "projectId": org["project"],
            "targetId": "slack",
        },
    )
    assert r.status_code == 201, r.text
    assert r.json()["payload"]["targetId"] == "slack"


@pytest.mark.asyncio
async def test_model_provider_access_request_carries_provider_kind(org):
    """A client-raised model_provider_access request must record WHICH
    provider kind it's about. NOTE: this type does NOT carry a specific
    model_providers row id — the UI that raises it (model-availability-card.tsx)
    only ever has a (provider, model_id) pair in scope, never a connection's
    row id (that id is knowable only from Model Management's admin view, which
    a BU Admin raising this request is not looking at). The effect (Task 5)
    resolves the real row server-side, by provider kind, at decide time —
    this is a genuine design correction from the plan's first draft, which
    incorrectly assumed a row id was available here."""
    bu_admin = f"bu-{_uuid.uuid4()}"
    await _bind(org, bu_admin, "bu_admin", scope_kind="business_unit", scope_id=org["bu"])

    c = TestClient(process_api.app)
    headers = {"Authorization": "Bearer " + create_access_token(
        user_id=bu_admin, tenant_id=org["org"], permissions=["artifact:view", "model:manage"],
    )}
    r = c.post(
        "/governance-approvals", headers=headers,
        json={
            "type": "model_provider_access", "title": "Onboard Anthropic",
            "description": "Need Claude for the security agent.", "priority": "normal",
            "workspaceId": org["bu"], "providerModel": {"provider": "anthropic", "modelId": "claude-sonnet-5"},
        },
    )
    assert r.status_code == 201, r.text
    assert r.json()["payload"]["providerModel"]["provider"] == "anthropic"


# ── the gate: connector_access and model_provider_access actually apply ─────

@pytest.mark.asyncio
async def test_connector_access_request_grants_on_approval(org):
    """The exact bug this plan fixes: raised with a real targetId, approved,
    and the grant must actually land in project_connector_access.

    connector_access is TIER-ROUTED (absent from routing.TYPE_ROUTED), so a
    Developer's request lands on their Project Admin, not the Org Admin —
    confirm this against routing.py before changing the shape below. With
    `projectId` set, `_apply_connector_access` takes its PROJECT branch,
    which requires the connector already granted to the business unit
    (`integration_grants`) — seeded directly here rather than through a
    second request, since that grant is a precondition of this test, not
    what it's testing.
    """
    dev = f"dev-{_uuid.uuid4()}"
    await _bind(org, dev, "developer", scope_kind="project", scope_id=org["project"])
    project_admin = f"pa-{_uuid.uuid4()}"
    await _bind(org, project_admin, "project_admin", scope_kind="project", scope_id=org["project"])
    async with get_db_session_for_tenant(org["org"]) as s:
        await s.execute(text(
            "INSERT INTO integration_grants (tenant_id, kind, target_ref, workspace_id) "
            "VALUES (CAST(:t AS uuid), 'connector', 'slack', CAST(:w AS uuid))"
        ), {"t": org["org"], "w": org["bu"]})

    c = TestClient(process_api.app)
    dev_headers = {"Authorization": "Bearer " + create_access_token(
        user_id=dev, tenant_id=org["org"], permissions=["artifact:view", "run:create"],
    )}
    raised = c.post(
        "/governance-approvals", headers=dev_headers,
        json={
            "type": "connector_access", "title": "Slack access", "description": "For releases.",
            "priority": "normal", "workspaceId": org["bu"], "projectId": org["project"],
            # Slack has no read capabilities (see connector_capabilities.py's own
            # docstring) — "write" is the level it can actually honour, and the
            # brief's original "read" trips the manifest check this same effect
            # enforces, for reasons that have nothing to do with the bug under test.
            "targetId": "slack", "accessLevel": "write",
        },
    )
    assert raised.status_code == 201, raised.text
    assert raised.json()["currentApproverRole"] == "project_admin"

    pa_headers = {"Authorization": "Bearer " + create_access_token(
        user_id=project_admin, tenant_id=org["org"],
        permissions=["artifact:view", "governance:decide"],
    )}
    decided = c.post(
        f"/governance-approvals/{raised.json()['id']}/decide", headers=pa_headers,
        json={"decision": "approve"},
    )
    assert decided.status_code == 200, decided.text
    async with get_db_session_for_tenant(org["org"]) as s:
        row = (await s.execute(text(
            "SELECT 1 FROM project_connector_access WHERE tenant_id = CAST(:t AS uuid) "
            "  AND project_id = CAST(:p AS uuid) AND kind = 'connector' AND target_ref = 'slack'"
        ), {"t": org["org"], "p": org["project"]})).first()
    assert row is not None, "connector_access approval did not grant project_connector_access"


@pytest.mark.asyncio
async def test_mcp_server_request_grants_on_approval(org):
    """Same shape as connector_access's test above: approving must actually grant the
    server to the business unit, not just record agreement.

    mcp_server's payload never carries an access level — `governance_requests.
    create_request` merges `access` only for `connector_access` (see its own comment
    there). And the manual write path this mirrors (`POST /integrations/access`,
    shared/routers/integration_access.py's `grant_integration_access`) requires an
    Organization Admin for EVERY kind it accepts, `mcp` included — `_require_org_admin`
    has no kind-specific carve-out, so an approval taking the same door has to hold to
    the same rule. That means mcp_server's effect has only the "unit" shape
    connector_access's effect has two of, and unlike connector_access's dev ->
    project_admin test above, the approver here has to actually BE org_admin for the
    grant to apply — raising as a bu_admin lands the approver there directly via tier
    routing (mcp_server is absent from routing.TYPE_ROUTED, same as connector_access;
    confirmed in routing.py), with no escalation call needed.
    """
    server_id = str(_uuid.uuid4())
    async with get_db_session_for_tenant(org["org"]) as s:
        await s.execute(text(
            "INSERT INTO mcp_servers (id, tenant_id, server_name, transport, url, created_by) "
            "VALUES (CAST(:i AS uuid), CAST(:t AS uuid), 'Internal Docs', 'streamable_http', "
            "  'https://mcp.internal.example/docs', 'seed')"
        ), {"i": server_id, "t": org["org"]})

    bu_admin = f"bu-{_uuid.uuid4()}"
    await _bind(org, bu_admin, "bu_admin", scope_kind="business_unit", scope_id=org["bu"])
    org_admin = f"org-{_uuid.uuid4()}"
    await _bind(org, org_admin, "org_admin", scope_kind="organization", scope_id=org["org"])

    c = TestClient(process_api.app)
    bu_headers = {"Authorization": "Bearer " + create_access_token(
        user_id=bu_admin, tenant_id=org["org"], permissions=["artifact:view", "run:create"],
    )}
    raised = c.post(
        "/governance-approvals", headers=bu_headers,
        json={
            "type": "mcp_server", "title": "Internal Docs access", "description": "For runbooks.",
            "priority": "normal", "workspaceId": org["bu"], "targetId": server_id,
        },
    )
    assert raised.status_code == 201, raised.text
    assert raised.json()["currentApproverRole"] == "org_admin"

    org_headers = {"Authorization": "Bearer " + create_access_token(
        user_id=org_admin, tenant_id=org["org"], permissions=["artifact:view", "governance:decide"],
    )}
    decided = c.post(
        f"/governance-approvals/{raised.json()['id']}/decide", headers=org_headers,
        json={"decision": "approve"},
    )
    assert decided.status_code == 200, decided.text
    async with get_db_session_for_tenant(org["org"]) as s:
        row = (await s.execute(text(
            "SELECT 1 FROM integration_grants WHERE tenant_id = CAST(:t AS uuid) "
            "  AND kind = 'mcp' AND target_ref = :r AND workspace_id = CAST(:w AS uuid)"
        ), {"t": org["org"], "r": server_id, "w": org["bu"]})).first()
    assert row is not None, "mcp_server approval did not grant integration_grants"


async def _seed_inactive_anthropic_provider(org):
    async with get_db_session_superuser() as s:
        await s.execute(text(
            "INSERT INTO model_providers (id, tenant_id, workspace_id, provider, display_name, "
            "  secret_ref, status, created_by) "
            "VALUES (gen_random_uuid(), CAST(:t AS uuid), NULL, 'anthropic', 'Anthropic', "
            "  '', 'unverified', 'seed')"
        ), {"t": org["org"]})


@pytest.mark.asyncio
async def test_model_provider_access_request_activates_on_approval(org):
    """Raise with a (provider, model_id) pair, no row id — the effect must
    find the matching inactive org-wide connection itself and activate it.
    model_provider_access IS type-routed straight to org_admin
    (routing.GOVERNANCE_APPROVER_ROLE), so a single decide call suffices —
    no escalation needed."""
    await _seed_inactive_anthropic_provider(org)
    bu_admin = f"bu-{_uuid.uuid4()}"
    await _bind(org, bu_admin, "bu_admin", scope_kind="business_unit", scope_id=org["bu"])
    org_admin = f"org-{_uuid.uuid4()}"
    await _bind(org, org_admin, "org_admin", scope_kind="organization", scope_id=org["org"])

    c = TestClient(process_api.app)
    bu_headers = {"Authorization": "Bearer " + create_access_token(
        user_id=bu_admin, tenant_id=org["org"], permissions=["artifact:view", "model:manage"],
    )}
    raised = c.post(
        "/governance-approvals", headers=bu_headers,
        json={
            "type": "model_provider_access", "title": "Onboard Anthropic",
            "description": "Need Claude for the security agent.", "priority": "normal",
            "workspaceId": org["bu"], "providerModel": {"provider": "anthropic", "modelId": "claude-sonnet-5"},
        },
    )
    assert raised.status_code == 201, raised.text
    assert raised.json()["currentApproverRole"] == "org_admin"

    org_headers = {"Authorization": "Bearer " + create_access_token(
        user_id=org_admin, tenant_id=org["org"], permissions=["artifact:view", "governance:decide"],
    )}
    decided = c.post(
        f"/governance-approvals/{raised.json()['id']}/decide", headers=org_headers,
        json={"decision": "approve"},
    )
    assert decided.status_code == 200, decided.text
    async with get_db_session_for_tenant(org["org"]) as s:
        row = (await s.execute(text(
            "SELECT status FROM model_providers WHERE tenant_id = CAST(:t AS uuid) "
            "  AND provider = 'anthropic' AND workspace_id IS NULL"
        ), {"t": org["org"]})).first()
    assert row is not None and row.status == "active"


@pytest.mark.asyncio
async def test_model_provider_access_refuses_when_ambiguous(org):
    """Two inactive anthropic connections exist for this tenant: the effect
    must refuse rather than guess which one the requester meant."""
    await _seed_inactive_anthropic_provider(org)
    await _seed_inactive_anthropic_provider(org)
    bu_admin = f"bu-{_uuid.uuid4()}"
    await _bind(org, bu_admin, "bu_admin", scope_kind="business_unit", scope_id=org["bu"])
    org_admin = f"org-{_uuid.uuid4()}"
    await _bind(org, org_admin, "org_admin", scope_kind="organization", scope_id=org["org"])

    c = TestClient(process_api.app)
    bu_headers = {"Authorization": "Bearer " + create_access_token(
        user_id=bu_admin, tenant_id=org["org"], permissions=["artifact:view", "model:manage"],
    )}
    raised = c.post(
        "/governance-approvals", headers=bu_headers,
        json={
            "type": "model_provider_access", "title": "Onboard Anthropic",
            "description": "Need Claude for the security agent.", "priority": "normal",
            "workspaceId": org["bu"], "providerModel": {"provider": "anthropic", "modelId": "claude-sonnet-5"},
        },
    )
    assert raised.status_code == 201, raised.text

    org_headers = {"Authorization": "Bearer " + create_access_token(
        user_id=org_admin, tenant_id=org["org"], permissions=["artifact:view", "governance:decide"],
    )}
    decided = c.post(
        f"/governance-approvals/{raised.json()['id']}/decide", headers=org_headers,
        json={"decision": "approve"},
    )
    assert 400 <= decided.status_code < 500, decided.text
    body = decided.json()
    assert body["detail"]["code"] == "EFFECT_UNAVAILABLE", body
    assert "anthropic" in body["detail"]["message"].lower()


@pytest.mark.asyncio
async def test_model_credential_request_selects_model_for_project(org):
    """Approving must add the requested (provider, model_id) to the project's
    selection — the same write set_project_selection already performs by hand.
    Requires the model already reachable to the project's BU (get_bu_allowed) —
    see model_grants.py's NotAllowedForUnitError for why. Seeded directly as a
    GLOBAL org_model_grants row (reaches every unit, including this one) —
    the simplest real precondition, matching set_org_grants's own INSERT
    shape rather than going through set_bu_grants's specific-visibility path,
    which this test has no need to exercise.

    model_credential is TIER-ROUTED (absent from routing.TYPE_ROUTED), so a
    Developer's request lands on their Project Admin directly — one decide
    call, no escalation.

    A `global` org_model_grants row alone is not enough to make the model
    reachable: get_bu_allowed (model_grants.py) requires BOTH the curation
    row AND the provider itself currently granted to the BU via
    `integration_grants(kind='model_provider')` — the same coupling
    test_model_grants.py's `_grant_provider` helper exists for. Without this
    second row the effect correctly refuses with NotAllowedForUnitError,
    which is not what this test is exercising."""
    async with get_db_session_for_tenant(org["org"]) as s:
        await s.execute(text(
            "INSERT INTO org_model_grants "
            "  (id, tenant_id, provider, model_id, credential_id, visibility, business_unit_ids, created_by) "
            "VALUES (gen_random_uuid(), CAST(:t AS uuid), 'openai', 'gpt-4.1', NULL, 'global', '[]', 'seed')"
        ), {"t": org["org"]})
        await s.execute(text(
            "INSERT INTO integration_grants (tenant_id, kind, target_ref, workspace_id, granted_by) "
            "VALUES (CAST(:t AS uuid), 'model_provider', 'openai', CAST(:w AS uuid), 'seed') "
            "ON CONFLICT (tenant_id, kind, target_ref, workspace_id) DO NOTHING"
        ), {"t": org["org"], "w": org["bu"]})
    dev = f"dev-{_uuid.uuid4()}"
    await _bind(org, dev, "developer", scope_kind="project", scope_id=org["project"])
    project_admin = f"pa-{_uuid.uuid4()}"
    await _bind(org, project_admin, "project_admin", scope_kind="project", scope_id=org["project"])

    c = TestClient(process_api.app)
    dev_headers = {"Authorization": "Bearer " + create_access_token(
        user_id=dev, tenant_id=org["org"], permissions=["artifact:view", "run:create"],
    )}
    raised = c.post(
        "/governance-approvals", headers=dev_headers,
        json={
            "type": "model_credential", "title": "Need GPT-4.1", "description": "For the design agent.",
            "priority": "normal", "workspaceId": org["bu"], "projectId": org["project"],
            "providerModel": {"provider": "openai", "modelId": "gpt-4.1"},
        },
    )
    assert raised.status_code == 201, raised.text
    assert raised.json()["currentApproverRole"] == "project_admin"

    pa_headers = {"Authorization": "Bearer " + create_access_token(
        user_id=project_admin, tenant_id=org["org"], permissions=["artifact:view", "governance:decide"],
    )}
    decided = c.post(
        f"/governance-approvals/{raised.json()['id']}/decide", headers=pa_headers,
        json={"decision": "approve"},
    )
    assert decided.status_code == 200, decided.text
    from shared.services.model_grants import get_project_selection
    selection = await get_project_selection(org["org"], org["project"])
    assert any(e["provider"] == "openai" and e["model_id"] == "gpt-4.1" for e in selection["selected"])
