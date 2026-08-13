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
    org_id, bu = str(_uuid.uuid4()), str(_uuid.uuid4())
    async with get_db_session_superuser() as s:
        await s.execute(text(
            "INSERT INTO organizations (id, slug, display_name) VALUES (:i, :s, 'Notif Test')"
        ), {"i": org_id, "s": f"ntf-{org_id[:8]}"})
        await s.execute(text(
            "INSERT INTO workspaces (id, organization_id, slug, display_name) "
            "VALUES (:i, :o, 'unit', 'Unit')"
        ), {"i": bu, "o": org_id})
    yield {"org": org_id, "bu": bu}


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
                       title="Waiting on you", recipient_role="bu_admin")

    async with get_db_session_for_tenant(org["org"]) as s:
        assert len(await svc.list_for(s, user_id="whoever", role="bu_admin")) == 1
        # A different role sees nothing, however senior.
        assert await svc.list_for(s, user_id="whoever", role="org_admin") == []


@pytest.mark.asyncio
async def test_a_viewer_sees_their_own_and_their_queue_together(org):
    """Both, not either — an approver who also raises requests needs one list."""
    async with get_db_session_for_tenant(org["org"]) as s:
        await svc.emit(s, tenant_id=org["org"], kind="request_approved",
                       title="Yours was approved", recipient_user_id="ana")
        await svc.emit(s, tenant_id=org["org"], kind="request_approval_required",
                       title="One waiting on you", recipient_role="project_admin")

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

    async with get_db_session_for_tenant(org["org"]) as s:
        # It routed to project_admin, so that queue hears about it...
        queue = await svc.list_for(s, user_id="somebody", role="project_admin")
        assert [n["kind"] for n in queue] == ["request_approval_required"]
        # ...and nobody else does.
        assert await svc.list_for(s, user_id="somebody", role="bu_admin") == []


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
