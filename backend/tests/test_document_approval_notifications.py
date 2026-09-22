"""Putting a document forward, and deciding it, both reach the bell.

The Requests & Approvals queue already listed a pending document. This file pins the
other half: the same two moments now also arrive as notifications, addressed and
deep-linked to the agent the document belongs to.

  raised   -> the project's administrators   (document_approval_required)
  approved -> the person who raised it       (document_approved)
  rejected -> the person who raised it       (document_rejected)
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
def _storage_is_not_what_this_file_is_about():
    """Approval promotes a document out of the `_pending` prefix, and these rows are
    inserted straight into the table with a blob_path and no bytes behind it.

    WHY THE FIXTURE EXISTS. Whether that promote runs at all depended on ambient state:
    `app.state.blob_client` is None until something starts the app's lifespan, so these
    tests passed alone and failed inside the full suite, where an earlier test had
    initialised it — and then the promote reached the real storage account looking for
    bytes nobody uploaded. Three tests about a QUEUE, failing on storage, in one order
    and not the other.

    A double that succeeds says what the file means: the document is approved, and what
    happens to its bytes is `test_artifact_approval`'s subject, not this one's.
    """
    class _Storage:
        async def move_blob(self, src, dst, content_type=None):
            return f"https://example.invalid/{dst}"

    previous = getattr(process_api.app.state, "blob_client", None)
    process_api.app.state.blob_client = _Storage()
    yield
    process_api.app.state.blob_client = previous


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
                    name="brd.pptx", uploaded_by="bruno@abcbank.com", note=None) -> str:
    art = str(_uuid.uuid4())
    async with get_db_session_for_tenant(project["org"]) as s:
        await s.execute(text(
            "INSERT INTO artifacts (id, project_id, stage, tenant_id, artifact_type, "
            "  blob_path, approval_status, uploaded_by, upload_note) "
            "VALUES (CAST(:i AS uuid), CAST(:p AS uuid), :st, CAST(:t AS uuid), "
            "  'document', :bp, :s, :ub, :nt)"
        ), {"i": art, "p": project["proj"], "st": stage, "t": project["org"],
            "bp": f"{project['org']}/bu/proj/{stage or '_none'}/x/document/{name}",
            "s": status, "ub": uploaded_by, "nt": note})
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




def _bell(user, project, perms):
    r = _client().get(
        "/notifications",
        headers=_headers(user, project["org"], project["bu"], perms))
    assert r.status_code == 200, r.text
    return r.json()


ADMIN_PERMS = ["artifact:view", "approve", "run:create"]
BA_PERMS = ["artifact:view", "run:create", "approve", "artifact:approve_requirements"]


@pytest.mark.asyncio
async def test_raising_a_document_notifies_the_project_admin_with_a_link_to_the_agent(project):
    art = await _document(project, status="draft", name="brd-final.docx")
    ba = await _ba(project)
    admin = await _project_admin(project)

    r = _client().post(
        f"/artifacts/{art}/submit",
        headers=_headers(ba, project["org"], project["bu"], BA_PERMS))
    assert r.status_code == 200, r.text

    rows = [n for n in _bell(admin, project, ADMIN_PERMS) if n["kind"] == "document_approval_required"]
    assert len(rows) == 1
    assert "brd-final.docx" in rows[0]["title"]
    assert rows[0]["href"] == f"/projects/{project['proj']}/requirements"


@pytest.mark.asyncio
async def test_a_code_review_document_links_to_the_hyphenated_route(project):
    art = await _document(project, stage="code_review", status="draft", name="review.docx")
    ba = await _ba(project)
    admin = await _project_admin(project)
    perms = ["artifact:view", "run:create"]

    r = _client().post(
        f"/artifacts/{art}/submit",
        headers=_headers(ba, project["org"], project["bu"], perms))
    assert r.status_code == 200, r.text

    rows = [n for n in _bell(admin, project, ADMIN_PERMS) if n["kind"] == "document_approval_required"]
    assert [n["href"] for n in rows] == [f"/projects/{project['proj']}/code-review"]


@pytest.mark.asyncio
async def test_raising_twice_notifies_once(project):
    art = await _document(project, status="draft")
    ba = await _ba(project)
    admin = await _project_admin(project)
    hdr = _headers(ba, project["org"], project["bu"], BA_PERMS)

    _client().post(f"/artifacts/{art}/submit", headers=hdr)
    _client().post(f"/artifacts/{art}/submit", headers=hdr)

    rows = [n for n in _bell(admin, project, ADMIN_PERMS) if n["kind"] == "document_approval_required"]
    assert len(rows) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("decision,kind", [("approve", "document_approved"), ("reject", "document_rejected")])
async def test_a_decision_notifies_the_person_who_raised_it(project, decision, kind):
    art = await _document(project, status="draft", name="design.docx")
    ba = await _ba(project)
    admin = await _project_admin(project)

    assert _client().post(
        f"/artifacts/{art}/submit",
        headers=_headers(ba, project["org"], project["bu"], BA_PERMS)).status_code == 200

    body = {"reason": "needs work"} if decision == "reject" else None
    r = _client().post(
        f"/artifacts/{art}/{decision}", json=body,
        headers=_headers(admin, project["org"], project["bu"], ADMIN_PERMS))
    assert r.status_code == 200, r.text

    rows = [n for n in _bell(ba, project, BA_PERMS) if n["kind"] == kind]
    assert len(rows) == 1
    assert "design.docx" in rows[0]["title"]
    assert rows[0]["href"] == f"/projects/{project['proj']}/requirements"
    if decision == "reject":
        assert "needs work" in rows[0]["body"]


@pytest.mark.asyncio
async def test_deciding_your_own_submission_does_not_notify_you(project):
    art = await _document(project, status="draft")
    ba = await _ba(project)
    hdr = _headers(ba, project["org"], project["bu"], BA_PERMS)

    _client().post(f"/artifacts/{art}/submit", headers=hdr)
    assert _client().post(f"/artifacts/{art}/approve", headers=hdr).status_code == 200

    assert [n for n in _bell(ba, project, BA_PERMS) if n["kind"] == "document_approved"] == []
