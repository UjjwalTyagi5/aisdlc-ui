"""Approval requests, and the self-approval block.

The block is asserted twice on purpose: once through the service, and once over real
HTTP with no UI involved. A rule that only holds when called the way the UI calls it
is not a rule — the HTTP test is the one that proves a direct API caller cannot get
around it.
"""
import uuid as _uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

import process_api
from config.auth.jwt import create_access_token
from shared.db import get_db_session_for_tenant, get_db_session_superuser
from shared.services import approval_requests as svc
from shared.services.approval_requests import (
    ApprovalAlreadyDecided,
    ApprovalNotFound,
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
    org_id, bu = str(_uuid.uuid4()), str(_uuid.uuid4())
    async with get_db_session_superuser() as s:
        await s.execute(text(
            "INSERT INTO organizations (id, slug, display_name) VALUES (:i, :s, 'Approval Test')"
        ), {"i": org_id, "s": f"appr-{org_id[:8]}"})
        await s.execute(text(
            "INSERT INTO workspaces (id, organization_id, slug, display_name) "
            "VALUES (:i, :o, 'unit', 'Unit')"
        ), {"i": bu, "o": org_id})
    yield {"org": org_id, "bu": bu}


async def _raise_request(org: dict, initiator: str) -> dict:
    async with get_db_session_for_tenant(org["org"]) as s:
        return await svc.create_request(
            s,
            tenant_id=org["org"],
            initiator_id=initiator,
            subject_kind="model_grant",
            subject_id="claude-sonnet-4-6",
            title="Access to Sonnet",
            target_role="bu_admin",
            scope_kind="business_unit",
            scope_id=org["bu"],
        )


# ── the rule, in the service ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_initiator_cannot_decide_their_own_request(org):
    alice = f"alice-{_uuid.uuid4()}"
    req = await _raise_request(org, alice)

    async with get_db_session_for_tenant(org["org"]) as s:
        with pytest.raises(SelfApprovalBlocked) as ei:
            await svc.decide(s, request_id=req["id"], decider_id=alice, decision="approved")
    assert ei.value.code == "SELF_APPROVAL_BLOCKED"
    assert ei.value.http_status == 400

    # Rejecting your own request is the same act with the opposite sign — a person who
    # can kill their own request unilaterally has bypassed the reviewer just as surely.
    async with get_db_session_for_tenant(org["org"]) as s:
        with pytest.raises(SelfApprovalBlocked):
            await svc.decide(s, request_id=req["id"], decider_id=alice, decision="rejected")

    # ...and the request is untouched.
    async with get_db_session_for_tenant(org["org"]) as s:
        assert (await svc.get_request(s, req["id"]))["status"] == "pending"


@pytest.mark.asyncio
async def test_someone_else_can_decide_it(org):
    alice, bob = f"alice-{_uuid.uuid4()}", f"bob-{_uuid.uuid4()}"
    req = await _raise_request(org, alice)

    async with get_db_session_for_tenant(org["org"]) as s:
        out = await svc.decide(
            s, request_id=req["id"], decider_id=bob, decision="approved", reason="looks fine"
        )
    assert out["status"] == "approved"
    assert out["decidedBy"] == bob
    assert out["decidedAt"] is not None
    assert out["decisionReason"] == "looks fine"


@pytest.mark.asyncio
async def test_a_decided_request_cannot_be_decided_again(org):
    alice, bob, carol = (f"{n}-{_uuid.uuid4()}" for n in ("alice", "bob", "carol"))
    req = await _raise_request(org, alice)
    async with get_db_session_for_tenant(org["org"]) as s:
        await svc.decide(s, request_id=req["id"], decider_id=bob, decision="approved")
    async with get_db_session_for_tenant(org["org"]) as s:
        with pytest.raises(ApprovalAlreadyDecided):
            await svc.decide(s, request_id=req["id"], decider_id=carol, decision="rejected")


@pytest.mark.asyncio
async def test_self_approval_is_reported_ahead_of_already_decided(org):
    """The more specific answer wins: 'you raised this' beats 'this is closed'."""
    alice, bob = f"alice-{_uuid.uuid4()}", f"bob-{_uuid.uuid4()}"
    req = await _raise_request(org, alice)
    async with get_db_session_for_tenant(org["org"]) as s:
        await svc.decide(s, request_id=req["id"], decider_id=bob, decision="approved")

    async with get_db_session_for_tenant(org["org"]) as s:
        with pytest.raises(SelfApprovalBlocked):
            await svc.decide(s, request_id=req["id"], decider_id=alice, decision="approved")


@pytest.mark.asyncio
async def test_unknown_request_is_not_found(org):
    async with get_db_session_for_tenant(org["org"]) as s:
        with pytest.raises(ApprovalNotFound):
            await svc.decide(
                s, request_id=str(_uuid.uuid4()), decider_id="bob", decision="approved"
            )
        with pytest.raises(ApprovalNotFound):
            await svc.decide(s, request_id="not-a-uuid", decider_id="bob", decision="approved")


@pytest.mark.asyncio
async def test_a_request_without_an_initiator_is_refused(org):
    """The column is NOT NULL, and the service refuses before reaching it: a request
    with no initiator would be permanently exempt from the self-approval rule."""
    async with get_db_session_for_tenant(org["org"]) as s:
        with pytest.raises(Exception) as ei:
            await svc.create_request(
                s, tenant_id=org["org"], initiator_id="", subject_kind="x",
                subject_id=None, title="t", target_role="bu_admin",
                scope_kind="business_unit", scope_id=org["bu"],
            )
    assert "initiator" in str(ei.value).lower()


# ── the rule, over real HTTP ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_self_approval_blocked_over_http_with_no_ui(org):
    """Create a request then approve it as the same user — must be 400."""
    alice = f"alice-{_uuid.uuid4()}"
    c = TestClient(process_api.app)
    headers = {
        "Authorization": "Bearer " + create_access_token(
            user_id=alice, tenant_id=org["org"], permissions=["artifact:view", "approve"]
        )
    }

    created = c.post("/approvals/requests", headers=headers, json={
        "subjectKind": "model_grant",
        "subjectId": "claude-sonnet-4-6",
        "title": "Access to Sonnet",
        "targetRole": "bu_admin",
        "scopeKind": "business_unit",
        "scopeId": org["bu"],
    })
    assert created.status_code == 201, created.text
    request_id = created.json()["id"]
    # The initiator comes from the session, not the body — so it cannot be spoofed.
    assert created.json()["initiatorId"] == alice

    r = c.post(f"/approvals/{request_id}/approve", headers=headers, json={})
    assert r.status_code == 400, r.text
    assert r.json()["detail"]["error"] == "SELF_APPROVAL_BLOCKED", r.json()


@pytest.mark.asyncio
async def test_another_user_approves_over_http(org):
    alice, bob = f"alice-{_uuid.uuid4()}", f"bob-{_uuid.uuid4()}"
    c = TestClient(process_api.app)

    def hdr(uid: str) -> dict:
        return {"Authorization": "Bearer " + create_access_token(
            user_id=uid, tenant_id=org["org"], permissions=["artifact:view", "approve"]
        )}

    created = c.post("/approvals/requests", headers=hdr(alice), json={
        "subjectKind": "model_grant", "title": "Access to Sonnet",
        "targetRole": "bu_admin", "scopeKind": "business_unit", "scopeId": org["bu"],
    })
    assert created.status_code == 201, created.text
    request_id = created.json()["id"]

    r = c.post(f"/approvals/{request_id}/approve", headers=hdr(bob), json={"reason": "ok"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "approved"
    assert r.json()["decidedBy"] == bob


@pytest.mark.asyncio
async def test_deciding_is_audited(org):
    alice, bob = f"alice-{_uuid.uuid4()}", f"bob-{_uuid.uuid4()}"
    c = TestClient(process_api.app)

    def hdr(uid: str) -> dict:
        return {"Authorization": "Bearer " + create_access_token(
            user_id=uid, tenant_id=org["org"], permissions=["artifact:view", "approve"]
        )}

    created = c.post("/approvals/requests", headers=hdr(alice), json={
        "subjectKind": "model_grant", "title": "Access", "targetRole": "bu_admin",
        "scopeKind": "business_unit", "scopeId": org["bu"],
    })
    request_id = created.json()["id"]
    c.post(f"/approvals/{request_id}/reject", headers=hdr(bob), json={"reason": "no"})

    async with get_db_session_for_tenant(org["org"]) as s:
        rows = (await s.execute(text(
            "SELECT actor_id, event_type, resource_id FROM audit_events "
            "WHERE event_type = 'approval.request.rejected'"
        ))).fetchall()
    assert len(rows) == 1, rows
    assert rows[0].actor_id == bob        # who decided
    assert rows[0].resource_id == alice   # who raised it


@pytest.mark.asyncio
async def test_a_pending_request_cannot_carry_a_decider(org):
    """DB-level: the constraint, not just the service, keeps decisions complete."""
    alice = f"alice-{_uuid.uuid4()}"
    req = await _raise_request(org, alice)
    async with get_db_session_for_tenant(org["org"]) as s:
        with pytest.raises(Exception) as ei:
            await s.execute(text(
                "UPDATE approval_requests SET decided_by = 'ghost' WHERE id = CAST(:i AS uuid)"
            ), {"i": req["id"]})
    assert "ck_approval_request_decision_complete" in str(ei.value)
