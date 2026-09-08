"""A pending document appears in the approvals queue, and leaves it the moment it is decided.

WHAT WAS BROKEN. Uploading a document to a stage raises a real approval —
`artifacts.approval_status = 'pending'`, decided by `POST /artifacts/{id}/approve` — but
`GET /approvals` derived its rows from `runs.gate_pending` alone. So the document showed
up only in the Documents panel of the one stage page it belonged to, and the person whose
job is to approve things went to Requests & Approvals, the page that exists to answer
"what is waiting on me", and was told nothing was.

Nothing errored. The queue was simply, quietly, incomplete — which is why it survived:
every test of the queue passed, because every test was about run gates.

THE FIRST-APPROVER RULE IS THE OTHER HALF, and it is why this is DERIVED rather than
stored. A document has two equal approvers: the stage's owning role and the project's
administrator, either of whom may decide it. Had the queue materialised a task per
approver, approving as one would leave the other's copy behind — a row offering a
decision that has already been taken. Reading `approval_status` at query time means there
is no second copy to withdraw. The tests below assert that consequence directly, from
both approvers' points of view, because "we derive it" is an implementation note and
"it disappears from the other person's queue" is the promise.
"""
from __future__ import annotations

import sys
import uuid as _uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import process_api  # noqa: E402
from config.auth.jwt import create_access_token  # noqa: E402
from shared.authz.grant import grant_role  # noqa: E402
from shared.db import get_db_session_for_tenant, get_db_session_superuser  # noqa: E402

pytestmark = pytest.mark.usefixtures("purge_created_orgs")


@pytest.fixture(autouse=True)
async def _dispose_shared_engine():
    yield
    from shared.db import engine
    await engine.dispose()


@pytest.fixture
async def project():
    org, bu, proj = str(_uuid.uuid4()), str(_uuid.uuid4()), str(_uuid.uuid4())
    async with get_db_session_superuser() as s:
        await s.execute(text(
            "INSERT INTO organizations (id, slug, display_name) VALUES (:i, :s, 'Doc Queue')"
        ), {"i": org, "s": f"dq-{org[:8]}"})
        await s.execute(text(
            "INSERT INTO workspaces (id, organization_id, slug, display_name) "
            "VALUES (:i, :o, 'unit', 'Unit')"
        ), {"i": bu, "o": org})
    async with get_db_session_for_tenant(org) as s:
        await s.execute(text(
            "INSERT INTO projects (id, workspace_id, tenant_id, display_name) "
            "VALUES (:i, :w, :t, 'Queued')"
        ), {"i": proj, "w": bu, "t": org})

    yield {"org": org, "bu": bu, "proj": proj}

    async with get_db_session_for_tenant(org) as s:
        await s.execute(text("DELETE FROM role_bindings"))
        await s.execute(text("DELETE FROM artifacts"))
        await s.execute(text("DELETE FROM projects"))
    async with get_db_session_superuser() as s:
        await s.execute(text(
            "DELETE FROM workspaces WHERE organization_id = CAST(:t AS uuid)"), {"t": org})
        await s.execute(text(
            "DELETE FROM organizations WHERE id = CAST(:t AS uuid)"), {"t": org})


def _client() -> TestClient:
    return TestClient(process_api.app)


def _headers(user_id: str, org: str, bu: str, permissions: list[str]) -> dict:
    return {
        "Authorization": "Bearer "
        + create_access_token(user_id=user_id, tenant_id=org, permissions=permissions),
        "X-Workspace-Id": bu,
    }


async def _document(project, *, stage="requirements", status="pending",
                    name="brd.pptx", uploaded_by="bruno@abcbank.com") -> str:
    art = str(_uuid.uuid4())
    async with get_db_session_for_tenant(project["org"]) as s:
        await s.execute(text(
            "INSERT INTO artifacts (id, project_id, stage, tenant_id, artifact_type, "
            "  blob_path, approval_status, uploaded_by) "
            "VALUES (CAST(:i AS uuid), CAST(:p AS uuid), :st, CAST(:t AS uuid), "
            "  'document', :bp, :s, :ub)"
        ), {"i": art, "p": project["proj"], "st": stage, "t": project["org"],
            "bp": f"{project['org']}/bu/proj/{stage or '_none'}/x/document/{name}",
            "s": status, "ub": uploaded_by})
    return art


async def _ba(project) -> str:
    """The stage's owning role for requirements."""
    user = f"ba-{_uuid.uuid4()}"
    await grant_role(user, project["proj"], "ba",
                     tenant_id=project["org"], scope_kind="project")
    return user


async def _project_admin(project) -> str:
    user = f"pa-{_uuid.uuid4()}"
    await grant_role(user, project["proj"], "project_admin",
                     tenant_id=project["org"], scope_kind="project")
    return user


def _queue(user, project, perms):
    r = _client().get(
        "/approvals",
        headers=_headers(user, project["org"], project["bu"], perms))
    assert r.status_code == 200, r.text
    return r.json()


# -- it is in the queue at all ------------------------------------------------


@pytest.mark.asyncio
async def test_a_pending_document_is_in_the_queue(project):
    """THE HEADLINE. The queue returned only run gates, so this list was empty."""
    art = await _document(project)
    user = await _ba(project)

    rows = _queue(user, project, ["artifact:view", "artifact:approve_requirements"])

    assert [r["id"] for r in rows] == [f"artifact:{art}"]
    row = rows[0]
    assert row["type"] == "document"
    assert row["requiredPermission"] == "artifact:approve_requirements"
    # Named so an approver can tell an expected upload from one to ask about — unlike a
    # run gate, which is always raised by the agent.
    assert row["requestedBy"] == "bruno@abcbank.com"
    assert row["artifact"]["id"] == art
    assert "brd.pptx" in row["title"]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["approved", "rejected"])
async def test_a_decided_document_is_not_in_the_queue(project, status):
    """NON-VACUITY, and the shape of the whole feature: the queue lists what is
    PENDING, not every document that ever existed."""
    await _document(project, status=status)
    user = await _ba(project)

    assert _queue(user, project, ["artifact:view", "artifact:approve_requirements"]) == []


@pytest.mark.asyncio
async def test_a_story_row_never_reaches_the_queue(project):
    """`story` rows are projections of a run's requirements payload — synthesised ids,
    no blob, no approver. A queue row for one could never be cleared by anybody, so it
    would sit there forever."""
    async with get_db_session_for_tenant(project["org"]) as s:
        await s.execute(text(
            "INSERT INTO artifacts (id, project_id, stage, tenant_id, artifact_type, "
            "  blob_path, approval_status) VALUES (gen_random_uuid(), CAST(:p AS uuid), "
            "  'requirements', CAST(:t AS uuid), 'story', :bp, 'pending')"
        ), {"p": project["proj"], "t": project["org"], "bp": "x/story/s.json"})
    user = await _ba(project)

    assert _queue(user, project, ["artifact:view", "artifact:approve_requirements"]) == []


@pytest.mark.asyncio
async def test_a_project_wide_document_names_the_administrators_permission(project):
    """It belongs to no stage, so there is no owning role to route it to and no phase
    to label it with. `approve` is what project administration carries."""
    await _document(project, stage=None, name="policy.pdf")
    user = await _project_admin(project)

    rows = _queue(user, project, ["artifact:view", "approve"])

    assert len(rows) == 1
    assert rows[0]["requiredPermission"] == "approve"
    assert rows[0]["phase"] is None
    assert rows[0]["runId"] is None


# -- the first approver clears it for the second ------------------------------


@pytest.mark.asyncio
async def test_approving_removes_it_from_the_other_approvers_queue(project):
    """THE FIRST-APPROVER RULE, from the second approver's point of view.

    The stage's owning role and the project's administrator are equal approvers —
    whoever gets there first decides, and no second approval is required. So the row
    must leave the OTHER person's queue, not just the one who acted.

    This is the test that would fail if the queue ever materialised a task per approver
    instead of reading `approval_status`, which is the natural thing to build when
    someone asks for an inbox."""
    art = await _document(project)
    ba = await _ba(project)
    admin = await _project_admin(project)
    # The permissions a real BA and a real Project Admin actually carry — both hold
    # `approve`, which is the artifact decision route's floor, and only the BA holds
    # the requirements-stage permission.
    ba_perms = ["artifact:view", "approve", "artifact:approve_requirements"]
    admin_perms = ["artifact:view", "approve"]

    # Both see it while it is pending.
    assert len(_queue(ba, project, ba_perms)) == 1
    assert len(_queue(admin, project, admin_perms)) == 1

    # The BA gets there first.
    r = _client().post(
        f"/artifacts/{art}/approve",
        headers=_headers(ba, project["org"], project["bu"], ba_perms))
    assert r.status_code == 200, r.text

    # And it is gone for BOTH, without anybody withdrawing anything.
    assert _queue(ba, project, ba_perms) == []
    assert _queue(admin, project, admin_perms) == []


@pytest.mark.asyncio
async def test_the_administrator_can_be_the_first_approver_too(project):
    """NON-VACUITY on "equal": the rule is not "the stage owner decides and the admin
    is a fallback". Either may go first, and the test above would pass on a system
    where only the BA could ever act."""
    art = await _document(project)
    ba = await _ba(project)
    admin = await _project_admin(project)
    ba_perms = ["artifact:view", "approve", "artifact:approve_requirements"]
    admin_perms = ["artifact:view", "approve"]

    r = _client().post(
        f"/artifacts/{art}/approve",
        headers=_headers(admin, project["org"], project["bu"], admin_perms))
    assert r.status_code == 200, r.text

    assert _queue(ba, project, ba_perms) == []
    assert _queue(admin, project, admin_perms) == []


@pytest.mark.asyncio
async def test_rejecting_clears_it_as_well(project):
    """A decision is a decision. Rejection is not "still waiting" — leaving it in the
    queue would ask somebody to approve a document its owner has already refused."""
    art = await _document(project)
    ba = await _ba(project)
    perms = ["artifact:view", "approve", "artifact:approve_requirements"]

    r = _client().post(
        f"/artifacts/{art}/reject",
        headers=_headers(ba, project["org"], project["bu"], perms),
        json={"reason": "wrong template"})
    assert r.status_code == 200, r.text

    assert _queue(ba, project, perms) == []


# -- scope still applies ------------------------------------------------------


@pytest.mark.asyncio
async def test_a_document_in_another_unit_is_not_in_the_queue(project):
    """The new source is scoped by the same `allowed_workspace_ids` the run gates use,
    so adding it cannot widen what anybody sees."""
    other_unit, other_proj = str(_uuid.uuid4()), str(_uuid.uuid4())
    async with get_db_session_superuser() as s:
        await s.execute(text(
            "INSERT INTO workspaces (id, organization_id, slug, display_name) "
            "VALUES (:i, :o, 'other', 'Other')"), {"i": other_unit, "o": project["org"]})
    async with get_db_session_for_tenant(project["org"]) as s:
        await s.execute(text(
            "INSERT INTO projects (id, workspace_id, tenant_id, display_name) "
            "VALUES (:i, :w, :t, 'Elsewhere')"
        ), {"i": other_proj, "w": other_unit, "t": project["org"]})
        await s.execute(text(
            "INSERT INTO artifacts (id, project_id, stage, tenant_id, artifact_type, "
            "  blob_path, approval_status) VALUES (gen_random_uuid(), CAST(:p AS uuid), "
            "  'requirements', CAST(:t AS uuid), 'document', :bp, 'pending')"
        ), {"p": other_proj, "t": project["org"], "bp": "x/document/theirs.pdf"})

    # Bound only to the first project's unit.
    user = await _ba(project)

    rows = _queue(user, project, ["artifact:view", "artifact:approve_requirements"])

    assert [r["projectId"] for r in rows] == []


@pytest.mark.asyncio
async def test_the_metrics_tile_counts_the_same_things_the_queue_lists(project):
    """A summary that contradicts what it summarises is worse than no summary.

    `/approvals/metrics` counted `_pending_gates` alone and filtered on
    `type == "approval"`, so a project whose only pending item was a document reported
    "0 approvals pending" on the dashboard while the queue underneath showed it.
    """
    await _document(project)
    user = await _ba(project)
    perms = ["artifact:view", "artifact:approve_requirements"]

    rows = _queue(user, project, perms)
    r = _client().get(
        "/approvals/metrics",
        headers=_headers(user, project["org"], project["bu"], perms))
    assert r.status_code == 200, r.text

    assert len(rows) == 1
    assert r.json()["approvals"] == len(rows)


@pytest.mark.asyncio
async def test_the_metrics_tile_clears_with_the_queue(project):
    """NON-VACUITY: the count follows the decision, rather than being a constant that
    happened to match once."""
    art = await _document(project)
    user = await _ba(project)
    perms = ["artifact:view", "approve", "artifact:approve_requirements"]
    hdr = _headers(user, project["org"], project["bu"], perms)

    assert _client().post(f"/artifacts/{art}/approve", headers=hdr).status_code == 200

    assert _client().get("/approvals/metrics", headers=hdr).json()["approvals"] == 0
