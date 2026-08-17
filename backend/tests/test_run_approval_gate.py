"""Recording an approval requires the authority to approve.

`POST /runs/{run_id}/approvals` writes an `AuditEvent` of type `run.approved` naming
the caller as actor. It had no permission check beyond the router's `artifact:view`
floor — the one permission EVERY role holds, including `contributor`, whose whole
purpose is holding nothing until a unit admin assigns a real role. So any signed-in
account could record "this run was approved, by me" against any run in the tenant.

Two things made it easy to miss. It does not advance the run, so nothing visibly
changed. And `audit_events` is append-only by privilege (migration 0005 revokes UPDATE
and DELETE from `sdlc_app`), so a forged row could not be tidied up afterwards — the
property that makes the trail evidence is the same one that makes a bad write
permanent.

The gate mirrors `copilot_advance`: the stage the run is ACTUALLY at decides which
`artifact:approve_<phase>` permission is required.
"""
import uuid as _uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

import process_api
from config.auth.jwt import create_access_token
from shared.authz.grant import grant_role
from shared.db import get_db_session_for_tenant, get_db_session_superuser

pytestmark = pytest.mark.usefixtures("purge_created_orgs")


@pytest.fixture(autouse=True)
async def _dispose_shared_engine():
    yield
    from shared.db import engine
    await engine.dispose()


@pytest.fixture
async def run_at_requirements():
    """One org, one unit, one project, one run sitting at the requirements gate."""
    org, bu = str(_uuid.uuid4()), str(_uuid.uuid4())
    proj, run = str(_uuid.uuid4()), str(_uuid.uuid4())

    async with get_db_session_superuser() as s:
        await s.execute(text(
            "INSERT INTO organizations (id, slug, display_name) VALUES (:i, :s, 'Approval Gate')"
        ), {"i": org, "s": f"appr-{org[:8]}"})
        await s.execute(text(
            "INSERT INTO workspaces (id, organization_id, slug, display_name) "
            "VALUES (:i, :o, 'unit', 'Unit')"
        ), {"i": bu, "o": org})

    async with get_db_session_for_tenant(org) as s:
        await s.execute(text(
            "INSERT INTO projects (id, workspace_id, tenant_id, display_name) "
            "VALUES (:i, :w, :t, 'Gated')"
        ), {"i": proj, "w": bu, "t": org})
        await s.execute(text(
            "INSERT INTO runs (id, project_id, tenant_id, stage, status) "
            "VALUES (:i, :p, :t, 'requirements', 'running')"
        ), {"i": run, "p": proj, "t": org})

    yield {"org": org, "bu": bu, "proj": proj, "run": run}

    # audit_events is NOT cleaned up, and cannot be: migration 0005 revokes UPDATE and
    # DELETE on it from `sdlc_app`, so this teardown fails with "permission denied for
    # table audit_events" if it tries. That is the append-only property doing its job —
    # a trail a test could purge would not be evidence. The rows carry the throwaway
    # tenant_id and nothing has an FK onto them, so they are inert.
    async with get_db_session_for_tenant(org) as s:
        await s.execute(text("DELETE FROM role_bindings"))
        await s.execute(text("DELETE FROM runs"))
        await s.execute(text("DELETE FROM projects"))
    async with get_db_session_superuser() as s:
        await s.execute(text("DELETE FROM workspaces WHERE organization_id = CAST(:t AS uuid)"), {"t": org})
        await s.execute(text("DELETE FROM organizations WHERE id = CAST(:t AS uuid)"), {"t": org})


def _client() -> TestClient:
    return TestClient(process_api.app)


def _headers(user_id: str, org: str, bu: str, permissions: list[str]) -> dict:
    token = create_access_token(user_id=user_id, tenant_id=org, permissions=permissions)
    return {"Authorization": f"Bearer {token}", "X-Workspace-Id": bu}


async def _actor_who_can_see_the_run(t) -> str:
    """A user bound to the project, so scope resolution is not what refuses them."""
    user = f"actor-{_uuid.uuid4()}"
    await grant_role(user, t["proj"], "developer", tenant_id=t["org"], scope_kind="project")
    return user


async def _audit_count(org: str) -> int:
    async with get_db_session_for_tenant(org) as s:
        return (await s.execute(text(
            "SELECT count(*) FROM audit_events WHERE resource_type = 'run'"
        ))).scalar_one()


@pytest.mark.asyncio
async def test_the_view_floor_cannot_record_an_approval(run_at_requirements):
    """The bug: `artifact:view` is held by every role, so this was everybody."""
    t = run_at_requirements
    user = await _actor_who_can_see_the_run(t)

    c = _client()
    r = c.post(
        f"/runs/{t['run']}/approvals",
        headers=_headers(user, t["org"], t["bu"], ["artifact:view"]),
        json={"decision": "approve", "reason": "looks fine to me"},
    )
    assert r.status_code == 403, r.text


@pytest.mark.asyncio
async def test_nothing_is_written_when_the_approval_is_refused(run_at_requirements):
    """Fail BEFORE the write. audit_events is append-only — a forged row is forever."""
    t = run_at_requirements
    user = await _actor_who_can_see_the_run(t)
    before = await _audit_count(t["org"])

    c = _client()
    c.post(
        f"/runs/{t['run']}/approvals",
        headers=_headers(user, t["org"], t["bu"], ["artifact:view"]),
        json={"decision": "approve", "reason": "forged"},
    )
    assert await _audit_count(t["org"]) == before


@pytest.mark.asyncio
async def test_the_stages_approver_can_record_an_approval(run_at_requirements):
    """The run sits at `requirements`, which the BA owns."""
    t = run_at_requirements
    user = await _actor_who_can_see_the_run(t)

    c = _client()
    r = c.post(
        f"/runs/{t['run']}/approvals",
        headers=_headers(
            user, t["org"], t["bu"], ["artifact:view", "artifact:approve_requirements"]
        ),
        json={"decision": "approve", "reason": "requirements signed off"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["decision"] == "approve"


@pytest.mark.asyncio
async def test_another_stages_approver_cannot(run_at_requirements):
    """Holding SOME approve permission is not holding THIS one.

    A QA lead can sign off Testing and has no business signing off Requirements. This is
    the case a single generic `approve` permission could not express, and the reason the
    per-phase split is worth its cost.
    """
    t = run_at_requirements
    user = await _actor_who_can_see_the_run(t)

    c = _client()
    r = c.post(
        f"/runs/{t['run']}/approvals",
        headers=_headers(
            user, t["org"], t["bu"], ["artifact:view", "artifact:approve_testing"]
        ),
        json={"decision": "approve", "reason": "wrong stage"},
    )
    assert r.status_code == 403, r.text


@pytest.mark.asyncio
async def test_the_org_admin_wildcard_still_passes(run_at_requirements):
    """`admin:*` satisfies every check — the escape hatch stays open."""
    t = run_at_requirements
    user = await _actor_who_can_see_the_run(t)

    c = _client()
    r = c.post(
        f"/runs/{t['run']}/approvals",
        headers=_headers(user, t["org"], t["bu"], ["admin:*"]),
        json={"decision": "approve", "reason": "org admin override"},
    )
    assert r.status_code == 200, r.text
