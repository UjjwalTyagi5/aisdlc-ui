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


# -- closing the gate ---------------------------------------------------------
#
# THE HALF THAT WAS MISSING. A gate IS `runs.gate_pending = true` —
# `approvals._pending_gates` derives the whole queue from that column, and
# `_handle_artifact_ready` sets it when an agent finishes a stage. Clearing it lived in
# `copilot_advance`, which Phase 5A deleted with the rest of the Copilot, and nothing
# inherited the job. So this route recorded a decision beside a run that went on looking
# undecided, and the gate sat in every eligible queue permanently.


async def _gate_pending(org: str, run: str) -> bool:
    async with get_db_session_for_tenant(org) as s:
        return (await s.execute(
            text("SELECT gate_pending FROM runs WHERE id = CAST(:r AS uuid)"),
            {"r": run},
        )).scalar()


async def _raise_the_gate(org: str, run: str) -> None:
    """What `_handle_artifact_ready` does when an agent finishes a stage."""
    async with get_db_session_for_tenant(org) as s:
        await s.execute(
            text("UPDATE runs SET gate_pending = true, current_stage = 'requirements' "
                 "WHERE id = CAST(:r AS uuid)"),
            {"r": run},
        )
        await s.commit()


@pytest.mark.asyncio
async def test_approving_closes_the_gate(run_at_requirements):
    """Otherwise the queue row never clears and the same decision is asked for forever."""
    t = run_at_requirements
    await _raise_the_gate(t["org"], t["run"])
    assert await _gate_pending(t["org"], t["run"]) is True

    user = await _actor_who_can_see_the_run(t)
    r = _client().post(
        f"/runs/{t['run']}/approvals",
        headers=_headers(
            user, t["org"], t["bu"], ["artifact:view", "artifact:approve_requirements"]
        ),
        json={"decision": "approve", "reason": "signed off"},
    )

    assert r.status_code == 200, r.text
    assert await _gate_pending(t["org"], t["run"]) is False


@pytest.mark.asyncio
async def test_rejecting_closes_the_gate_too(run_at_requirements):
    """A rejection sends the stage back; it does not leave the run paused waiting for
    the same person to answer the same question again."""
    t = run_at_requirements
    await _raise_the_gate(t["org"], t["run"])

    user = await _actor_who_can_see_the_run(t)
    r = _client().post(
        f"/runs/{t['run']}/approvals",
        headers=_headers(
            user, t["org"], t["bu"], ["artifact:view", "artifact:approve_requirements"]
        ),
        json={"decision": "reject", "reason": "acceptance criteria are thin"},
    )

    assert r.status_code == 200, r.text
    assert await _gate_pending(t["org"], t["run"]) is False


@pytest.mark.asyncio
async def test_a_refused_decision_leaves_the_gate_open(run_at_requirements):
    """NON-VACUITY for the two above, and a rule in its own right: the gate closes
    because the decision was ACCEPTED, not merely because the route was called. A caller
    without the stage's permission must leave the run exactly as it was."""
    t = run_at_requirements
    await _raise_the_gate(t["org"], t["run"])

    user = await _actor_who_can_see_the_run(t)
    r = _client().post(
        f"/runs/{t['run']}/approvals",
        headers=_headers(user, t["org"], t["bu"], ["artifact:view"]),
        json={"decision": "approve"},
    )

    assert r.status_code == 403, r.text
    assert await _gate_pending(t["org"], t["run"]) is True


@pytest.mark.asyncio
async def test_the_past_tense_the_ui_sends_is_accepted(run_at_requirements):
    """The gate UI posts "approved"/"rejected" — that is what it sent to the retired
    copilot route, and three live screens still speak that way. Normalised rather than
    refused: teaching them a new vocabulary in the same change that fixes the routing
    would be two changes wearing one coat."""
    t = run_at_requirements
    await _raise_the_gate(t["org"], t["run"])

    user = await _actor_who_can_see_the_run(t)
    r = _client().post(
        f"/runs/{t['run']}/approvals",
        headers=_headers(
            user, t["org"], t["bu"], ["artifact:view", "artifact:approve_requirements"]
        ),
        json={"decision": "approved", "reason": "past tense from the queue"},
    )

    assert r.status_code == 200, r.text
    assert r.json()["decision"] == "approve"
    assert await _gate_pending(t["org"], t["run"]) is False


# -- the project admin is the stage owner's PEER -------------------------------
#
# A Project Admin owns every agent on their project by default, so they and the stage's
# own role are equal approvers: either may decide, whoever gets there first, and one
# decision closes the gate. `_artifact_for_decision` has always worked that way for
# documents; this route had only the stage-permission half, so a Project Admin could not
# sign off a stage on their own project — and the queue could not even show them the
# gate, because it lists what the viewer's permissions cover.


async def _project_admin(t) -> str:
    """Bound as `project_admin` ON THE PROJECT — administration, not a stage permission."""
    user = f"padmin-{_uuid.uuid4()}"
    await grant_role(user, t["proj"], "project_admin", tenant_id=t["org"], scope_kind="project")
    return user


@pytest.mark.asyncio
async def test_a_project_admin_can_approve_without_the_stage_permission(run_at_requirements):
    """THE POINT. No `artifact:approve_requirements` in the token at all — the token
    carries `approve`, which is what a Project Admin actually holds — and the decision is
    accepted because they administer the project."""
    t = run_at_requirements
    await _raise_the_gate(t["org"], t["run"])
    user = await _project_admin(t)

    r = _client().post(
        f"/runs/{t['run']}/approvals",
        headers=_headers(user, t["org"], t["bu"], ["artifact:view", "approve"]),
        json={"decision": "approve", "reason": "signed off by the project admin"},
    )

    assert r.status_code == 200, r.text
    assert await _gate_pending(t["org"], t["run"]) is False


@pytest.mark.asyncio
async def test_administering_a_DIFFERENT_project_is_not_enough(run_at_requirements):
    """NON-VACUITY, and the rule that keeps the widening narrow. Holding project_admin
    somewhere else in the tenant must not approve THIS project's gates — otherwise the
    second route would be "any admin anywhere", which is not what administering a project
    means."""
    t = run_at_requirements
    await _raise_the_gate(t["org"], t["run"])

    other_project = str(_uuid.uuid4())
    async with get_db_session_for_tenant(t["org"]) as s:
        await s.execute(text(
            "INSERT INTO projects (id, workspace_id, tenant_id, display_name) "
            "VALUES (:i, :w, :t, 'Somewhere Else')"
        ), {"i": other_project, "w": t["bu"], "t": t["org"]})
        await s.commit()

    user = f"elsewhere-{_uuid.uuid4()}"
    await grant_role(user, other_project, "project_admin", tenant_id=t["org"],
                     scope_kind="project")

    r = _client().post(
        f"/runs/{t['run']}/approvals",
        headers=_headers(user, t["org"], t["bu"], ["artifact:view", "approve"]),
        json={"decision": "approve"},
    )

    assert r.status_code in (403, 404), r.text
    assert await _gate_pending(t["org"], t["run"]) is True
