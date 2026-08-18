"""Notifications: who gets told, and who does not.

The interesting assertions are the negative ones. A bell that shows too much is not a
cosmetic problem — a request title says which unit is over budget, so an unaddressed
listing is a scope leak wearing a dropdown.
"""
import uuid as _uuid

import pytest
from sqlalchemy import text

from shared.db import get_db_session_for_tenant, get_db_session_superuser
from shared.services import governance_requests as governance
from shared.services import notifications as svc

pytestmark = pytest.mark.usefixtures("purge_created_orgs")


@pytest.fixture(autouse=True)
async def _dispose_shared_engine():
    yield
    from shared.db import engine
    await engine.dispose()


@pytest.fixture
async def org():
    """One organization, TWO business units, and a project in the first.

    Two units because one cannot show the bug this suite exists for: with a single
    unit, "every Business Unit Admin in the tenant" and "this unit's admin" are the
    same set, and a leak between units is invisible.
    """
    org_id = str(_uuid.uuid4())
    bu, other_bu, proj = str(_uuid.uuid4()), str(_uuid.uuid4()), str(_uuid.uuid4())
    async with get_db_session_superuser() as s:
        await s.execute(text(
            "INSERT INTO organizations (id, slug, display_name) VALUES (:i, :s, 'Notif Test')"
        ), {"i": org_id, "s": f"ntf-{org_id[:8]}"})
        for wid, slug, name in ((bu, "unit", "Unit"), (other_bu, "other", "Other Unit")):
            await s.execute(text(
                "INSERT INTO workspaces (id, organization_id, slug, display_name) "
                "VALUES (:i, :o, :s, :n)"
            ), {"i": wid, "o": org_id, "s": slug, "n": name})
    # projects is FORCE RLS — the insert needs the tenant GUC set, so it cannot ride
    # along with the organization and workspace rows above.
    async with get_db_session_for_tenant(org_id) as s:
        await s.execute(text(
            "INSERT INTO projects (id, workspace_id, tenant_id, display_name) "
            "VALUES (:i, :w, :t, 'Ledger')"
        ), {"i": proj, "w": bu, "t": org_id})
    yield {"org": org_id, "bu": bu, "other_bu": other_bu, "project": proj}


async def bind(org_id: str, user_id: str, role: str, scope_kind: str, scope_id: str):
    """Give somebody a role somewhere. Delivery is matched against real bindings, so
    a test that skips this is testing an account that holds nothing.

    Tenant session, not superuser: role_bindings is FORCE RLS too."""
    async with get_db_session_for_tenant(org_id) as s:
        await s.execute(text(
            "INSERT INTO role_bindings (id, user_id, scope_kind, scope_id, role_name, "
            "  status, tenant_id) "
            "VALUES (CAST(:i AS uuid), :u, :sk, CAST(:sid AS uuid), :r, 'active', "
            "        CAST(:t AS uuid))"
        ), {"i": str(_uuid.uuid4()), "u": user_id, "sk": scope_kind, "sid": scope_id,
            "r": role, "t": org_id})


# ── addressing ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_notification_addressed_to_a_person_reaches_only_them(org):
    async with get_db_session_for_tenant(org["org"]) as s:
        await svc.emit(s, tenant_id=org["org"], kind="request_approved",
                       title="Your budget request", recipient_user_id="alice")

    async with get_db_session_for_tenant(org["org"]) as s:
        assert len(await svc.list_for(s, user_id="alice", role="developer")) == 1
        assert await svc.list_for(s, user_id="bob", role="developer") == []


@pytest.mark.asyncio
async def test_a_notification_addressed_to_a_role_reaches_whoever_holds_it(org):
    """Including somebody appointed after it was written — the point of role
    addressing. A notification addressed to a predecessor is invisible to them."""
    async with get_db_session_for_tenant(org["org"]) as s:
        await svc.emit(s, tenant_id=org["org"], kind="request_approval_required",
                       title="Waiting on you", recipient_role="bu_admin",
                       recipient_scope_kind="business_unit", recipient_scope_id=org["bu"])

    # Appointed AFTER the notification was written, and still receives it.
    await bind(org["org"], "successor", "bu_admin", "business_unit", org["bu"])
    # In the same unit, holding a different role.
    await bind(org["org"], "dev", "developer", "business_unit", org["bu"])

    async with get_db_session_for_tenant(org["org"]) as s:
        assert len(await svc.list_for(s, user_id="successor", role="bu_admin")) == 1
        # Another role in the same unit sees nothing, and holding no binding at all
        # sees nothing however right the role name is.
        assert await svc.list_for(s, user_id="dev", role="developer") == []
        assert await svc.list_for(s, user_id="unbound", role="bu_admin") == []
        # And the acting role does NOT narrow a scoped row: the notification is for
        # the hat they hold, so it still reaches them while they act as something
        # else. Bindings decide, not `effective_platform_role`.
        assert len(await svc.list_for(s, user_id="successor", role="developer")) == 1


@pytest.mark.asyncio
async def test_a_units_queue_does_not_reach_another_units_admin(org):
    """THE LEAK THIS SCOPING EXISTS FOR. `recipient_role='bu_admin'` named a queue but
    not which one, so every Business Unit Admin in the tenant matched it — and a
    request title says which unit is over budget."""
    await bind(org["org"], "payments_admin", "bu_admin", "business_unit", org["bu"])
    await bind(org["org"], "lending_admin", "bu_admin", "business_unit", org["other_bu"])

    async with get_db_session_for_tenant(org["org"]) as s:
        await svc.emit(s, tenant_id=org["org"], kind="member_awaiting_role",
                       title="Ana needs a role", recipient_role="bu_admin",
                       recipient_scope_kind="business_unit", recipient_scope_id=org["bu"])

    async with get_db_session_for_tenant(org["org"]) as s:
        assert len(await svc.list_for(s, user_id="payments_admin", role="bu_admin")) == 1
        assert await svc.list_for(s, user_id="lending_admin", role="bu_admin") == []


@pytest.mark.asyncio
async def test_a_unit_queue_reaches_a_project_role_inside_it(org):
    """A queue addressed to the unit finds people bound to its projects — that is
    what "the unit's project admins" means. The sibling unit still hears nothing."""
    await bind(org["org"], "ana", "project_admin", "project", org["project"])
    await bind(org["org"], "sofia", "project_admin", "business_unit", org["other_bu"])

    async with get_db_session_for_tenant(org["org"]) as s:
        await svc.emit(s, tenant_id=org["org"], kind="request_approval_required",
                       title="Waiting on the unit's project admins",
                       recipient_role="project_admin",
                       recipient_scope_kind="business_unit", recipient_scope_id=org["bu"])

    async with get_db_session_for_tenant(org["org"]) as s:
        assert len(await svc.list_for(s, user_id="ana", role="project_admin")) == 1
        assert await svc.list_for(s, user_id="sofia", role="project_admin") == []


@pytest.mark.asyncio
async def test_a_project_queue_reaches_the_admin_of_its_unit(org):
    """And the containment holds the other way. A notification about one project
    addressed to the Business Unit Admin reaches the admin of the unit that project
    belongs to — their binding is at the unit, not the project."""
    await bind(org["org"], "payments_admin", "bu_admin", "business_unit", org["bu"])
    await bind(org["org"], "lending_admin", "bu_admin", "business_unit", org["other_bu"])

    async with get_db_session_for_tenant(org["org"]) as s:
        await svc.emit(s, tenant_id=org["org"], kind="budget_near_cap",
                       title="Ledger is near its cap", recipient_role="bu_admin",
                       recipient_scope_kind="project", recipient_scope_id=org["project"])

    async with get_db_session_for_tenant(org["org"]) as s:
        assert len(await svc.list_for(s, user_id="payments_admin", role="bu_admin")) == 1
        assert await svc.list_for(s, user_id="lending_admin", role="bu_admin") == []


@pytest.mark.asyncio
async def test_an_unscoped_role_address_is_refused_for_a_scoped_role(org):
    """The rule the 0022 CHECK constraint could not enforce, because rows written
    before it exist and cannot be attributed to a unit. Refused at the call site
    instead — an undeliverable notification beats one delivered to every unit."""
    async with get_db_session_for_tenant(org["org"]) as s:
        assert await svc.emit(
            s, tenant_id=org["org"], kind="member_awaiting_role",
            title="To every unit at once", recipient_role="bu_admin",
        ) is None

    await bind(org["org"], "payments_admin", "bu_admin", "business_unit", org["bu"])
    async with get_db_session_for_tenant(org["org"]) as s:
        assert await svc.list_for(s, user_id="payments_admin", role="bu_admin") == []


@pytest.mark.asyncio
async def test_the_organization_queue_needs_no_scope(org):
    """The one role whose queue is genuinely the whole organization. There is one
    Organization Admin queue, so it addresses without a scope and is matched against
    the role the caller acts as — org-wide standing is reachable through
    settings:manage with no binding at all, which a binding join would miss."""
    async with get_db_session_for_tenant(org["org"]) as s:
        assert await svc.emit(
            s, tenant_id=org["org"], kind="request_escalated",
            title="Escalated to you", recipient_role="org_admin",
        ) is not None

    async with get_db_session_for_tenant(org["org"]) as s:
        assert len(await svc.list_for(s, user_id="no_bindings", role="org_admin")) == 1
        assert await svc.list_for(s, user_id="no_bindings", role="bu_admin") == []


@pytest.mark.asyncio
async def test_a_viewer_sees_their_own_and_their_queue_together(org):
    """Both, not either — an approver who also raises requests needs one list."""
    async with get_db_session_for_tenant(org["org"]) as s:
        await svc.emit(s, tenant_id=org["org"], kind="request_approved",
                       title="Yours was approved", recipient_user_id="ana")
        await svc.emit(s, tenant_id=org["org"], kind="request_approval_required",
                       title="One waiting on you", recipient_role="project_admin",
                       recipient_scope_kind="project", recipient_scope_id=org["project"])

    await bind(org["org"], "ana", "project_admin", "project", org["project"])
    async with get_db_session_for_tenant(org["org"]) as s:
        assert len(await svc.list_for(s, user_id="ana", role="project_admin")) == 2


@pytest.mark.asyncio
async def test_a_notification_with_no_recipient_is_refused(org):
    """The CHECK constraint would refuse it too; the service refuses first so a
    caller that forgot the audience is told at the call site."""
    async with get_db_session_for_tenant(org["org"]) as s:
        assert await svc.emit(s, tenant_id=org["org"], kind="mention", title="To nobody") is None
        assert await svc.list_for(s, user_id="anyone", role="org_admin") == []


@pytest.mark.asyncio
async def test_marking_read_only_touches_what_you_can_see(org):
    async with get_db_session_for_tenant(org["org"]) as s:
        await svc.emit(s, tenant_id=org["org"], kind="request_approved",
                       title="Mine", recipient_user_id="alice")
        await svc.emit(s, tenant_id=org["org"], kind="request_approved",
                       title="Someone else's", recipient_user_id="bob")

    async with get_db_session_for_tenant(org["org"]) as s:
        assert await svc.mark_read(s, user_id="alice", role=None) == 1

    async with get_db_session_for_tenant(org["org"]) as s:
        assert (await svc.list_for(s, user_id="alice", role=None))[0]["readAt"] is not None
        assert (await svc.list_for(s, user_id="bob", role=None))[0]["readAt"] is None
        # Nothing left unread for alice, so a second sweep is a no-op.
        assert await svc.mark_read(s, user_id="alice", role=None) == 0


# ── the reason the table exists ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_raising_a_request_notifies_the_queue_it_landed_in(org):
    async with get_db_session_for_tenant(org["org"]) as s:
        await governance.create_request(
            s, tenant_id=org["org"], initiator_id="dev", initiator_name="Dev",
            initiator_role="developer", request_type="access_request",
            title="Need the deploy pipeline", description="Cannot ship without it.",
            workspace_id=org["bu"],
        )

    # The request named the unit but no project, so it is addressed to the unit's
    # project admins — which is anyone holding that role on a project inside it.
    await bind(org["org"], "somebody", "project_admin", "project", org["project"])
    await bind(org["org"], "unit_admin", "bu_admin", "business_unit", org["bu"])
    await bind(org["org"], "elsewhere", "project_admin", "business_unit", org["other_bu"])

    async with get_db_session_for_tenant(org["org"]) as s:
        # It routed to project_admin, so that queue hears about it...
        queue = await svc.list_for(s, user_id="somebody", role="project_admin")
        assert [n["kind"] for n in queue] == ["request_approval_required"]
        # ...and nobody else does — not another role in the same unit, and not the
        # same role in another unit.
        assert await svc.list_for(s, user_id="unit_admin", role="bu_admin") == []
        assert await svc.list_for(s, user_id="elsewhere", role="project_admin") == []


@pytest.mark.asyncio
async def test_deciding_a_request_notifies_the_person_who_raised_it(org):
    """The outcome follows the INITIATOR, wherever the request climbed to."""
    async with get_db_session_for_tenant(org["org"]) as s:
        req = await governance.create_request(
            s, tenant_id=org["org"], initiator_id="dev", initiator_name="Dev",
            initiator_role="developer", request_type="access_request",
            title="Need the deploy pipeline", description="Cannot ship without it.",
            workspace_id=org["bu"],
        )
    async with get_db_session_for_tenant(org["org"]) as s:
        await governance.decide(
            s, request_id=req["id"], decider_id="pa", decider_name="Pa",
            decider_role="project_admin", decision="approve", reason="Fair enough",
        )

    async with get_db_session_for_tenant(org["org"]) as s:
        mine = await svc.list_for(s, user_id="dev", role="developer")
    kinds = [n["kind"] for n in mine]
    assert "request_approved" in kinds
    assert any("Pa" in (n.get("body") or "") for n in mine)


@pytest.mark.asyncio
async def test_escalating_tells_both_the_new_queue_and_the_initiator(org):
    async with get_db_session_for_tenant(org["org"]) as s:
        req = await governance.create_request(
            s, tenant_id=org["org"], initiator_id="pa", initiator_name="Pa",
            initiator_role="project_admin", request_type="access_request",
            title="Need a wider grant", description="The unit was never given it.",
            workspace_id=org["bu"],
        )
    async with get_db_session_for_tenant(org["org"]) as s:
        await governance.escalate(
            s, request_id=req["id"], actor_id="pa", actor_name="Pa",
            actor_role="project_admin", note="No answer for a week",
        )

    async with get_db_session_for_tenant(org["org"]) as s:
        # The tier it climbed TO.
        assert any(n["kind"] == "request_escalated"
                   for n in await svc.list_for(s, user_id="x", role="org_admin"))
        # And the person actually waiting.
        assert any(n["kind"] == "request_escalated"
                   for n in await svc.list_for(s, user_id="pa", role="project_admin"))
