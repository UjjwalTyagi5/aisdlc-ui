"""A document belongs to a project and optionally an agent, and who may decide it.

PHASE 1 — SCOPE. Before migration 0052 a document's project and agent were recovered by
joining to the run that produced it, so a project-wide document could not exist and a
hand-uploaded one had nowhere to hang. `run_id` is nullable now, which means every read
path that dereferenced the run had to stop.

PHASE 2 — WHO APPROVES. Approval demanded `assert_can_administer_project` for
everything, so an Architect or QA who is not a project admin could not accept their own
stage's documents — the role that owns the work could not sign it off. Now:

    agent-level  (stage set)   the stage's owner OR project administration
    project-level (stage NULL)              project administration

PHASE 4 — UPLOAD. A person can put a document forward; it is PENDING until somebody
accepts it, exactly like a generated one.

Over real HTTP, because the thing most likely to be wrong on a gated route is the
authz wiring, and a service-level test cannot see it.
"""
from __future__ import annotations

import io
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
from shared.db import (  # noqa: E402
    get_db_session_for_tenant, get_db_session_superuser,
)

pytestmark = pytest.mark.usefixtures("purge_created_orgs")


@pytest.fixture(autouse=True)
async def _dispose_shared_engine():
    yield
    from shared.db import engine
    await engine.dispose()


@pytest.fixture
async def org_project():
    org, unit, project = str(_uuid.uuid4()), str(_uuid.uuid4()), str(_uuid.uuid4())
    async with get_db_session_superuser() as s:
        await s.execute(text(
            "INSERT INTO organizations (id, slug, display_name) "
            "VALUES (:i, :s, 'Artifact Scope Test')"
        ), {"i": org, "s": f"scope-{org[:8]}"})
        await s.execute(text(
            "INSERT INTO workspaces (id, organization_id, slug, display_name) "
            "VALUES (:i, :o, 'unit', 'Unit')"
        ), {"i": unit, "o": org})
    async with get_db_session_for_tenant(org) as s:
        await s.execute(text(
            "INSERT INTO projects (id, workspace_id, tenant_id, display_name) "
            "VALUES (:i, :w, :t, 'Scope Project')"
        ), {"i": project, "w": unit, "t": org})
    yield {"org": org, "unit": unit, "project": project}


def _hdr(user_id: str, org: str, perms: list[str]) -> dict:
    return {"Authorization": "Bearer " + create_access_token(
        user_id=user_id, tenant_id=org, permissions=perms)}


async def _member(t, role: str, perms: list[str]):
    user = f"{role}-{_uuid.uuid4()}"
    await grant_role(user, t["project"], role, tenant_id=t["org"], scope_kind="project")
    return user, _hdr(user, t["org"], perms)


def _upload(c, t, hdr, *, filename="brd.pdf", data=b"%PDF-1.4 hello", stage=None,
            artifact_type="document", note=None):
    form = {"artifact_type": artifact_type}
    if stage is not None:
        form["stage"] = stage
    if note is not None:
        form["note"] = note
    return c.post(
        f"/projects/{t['project']}/artifacts/upload",
        headers=hdr,
        files={"file": (filename, io.BytesIO(data), "application/pdf")},
        data=form,
    )


# -- phase 4: upload ----------------------------------------------------------


async def test_an_agent_level_document_records_its_stage(org_project):
    t = org_project
    _, hdr = await _member(t, "ba", ["run:create", "artifact:view"])
    with TestClient(process_api.app) as c:
        r = _upload(c, t, hdr, stage="requirements")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["scope"] == "agent" and body["stage"] == "requirements"
    assert body["runId"] is None, "a hand-uploaded document never had a run"


async def test_a_project_level_document_has_no_stage(org_project):
    """`stage` omitted means project-wide — a policy, a standard, not one agent's
    output."""
    t = org_project
    _, hdr = await _member(t, "ba", ["run:create", "artifact:view"])
    with TestClient(process_api.app) as c:
        r = _upload(c, t, hdr, filename="policy.pdf")
    assert r.status_code == 200, r.text
    assert r.json()["scope"] == "project"
    assert r.json()["stage"] is None


async def test_an_upload_is_pending_and_not_downloadable(org_project):
    """THE RULE THAT MAKES APPROVAL MEAN SOMETHING. One status for generated and
    uploaded documents alike; two would make 'approved' ambiguous on one screen."""
    t = org_project
    _, hdr = await _member(t, "ba", ["run:create", "artifact:view"])
    with TestClient(process_api.app) as c:
        r = _upload(c, t, hdr, stage="requirements")
        body = r.json()
        assert body["status"] == "awaiting_approval"
        assert body["approvedBy"] is None and body["approvedAt"] is None
        # The bytes are in the pending area, so there is nothing to fetch yet.
        assert body["body"]["awaitingApproval"] is True
        dl = c.get(f"/artifacts/{body['id']}/download", headers=hdr)
    assert dl.status_code != 200, "a pending document must not be downloadable"


async def test_the_uploader_is_recorded_apart_from_the_approver(org_project):
    """Collapsing the two would make self-approval invisible."""
    t = org_project
    user, hdr = await _member(t, "ba", ["run:create", "artifact:view"])
    with TestClient(process_api.app) as c:
        r = _upload(c, t, hdr, stage="requirements")
    assert r.json()["uploadedBy"] == user
    assert r.json()["approvedBy"] is None


@pytest.mark.parametrize("filename, data, status", [
    ("virus.exe", b"MZ", 400),          # not an allowed extension
    ("empty.pdf", b"", 400),            # nothing to store
])
async def test_a_refused_upload_says_why(org_project, filename, data, status):
    t = org_project
    _, hdr = await _member(t, "ba", ["run:create", "artifact:view"])
    with TestClient(process_api.app) as c:
        r = _upload(c, t, hdr, filename=filename, data=data)
    assert r.status_code == status, r.text
    assert r.json()["detail"], "a refusal with no reason leaves the user guessing"


async def test_an_unknown_stage_is_refused(org_project):
    """A typo would file the document under an agent nobody owns, and it would then
    silently need project administration to approve."""
    t = org_project
    _, hdr = await _member(t, "ba", ["run:create", "artifact:view"])
    with TestClient(process_api.app) as c:
        r = _upload(c, t, hdr, stage="not_a_stage")
    assert r.status_code == 422
    assert "not_a_stage" in r.text


async def test_uploading_needs_run_create(org_project):
    """Viewing a project is not the same as putting a document into its record."""
    t = org_project
    _, hdr = await _member(t, "developer", ["artifact:view"])
    with TestClient(process_api.app) as c:
        r = _upload(c, t, hdr, stage="requirements")
    assert r.status_code == 403


# -- phase 1: the scope is real, not derived ----------------------------------


async def test_a_project_level_document_is_not_returned_by_a_phase_filter(org_project):
    """It is not IN any phase. Returning it under one would imply an owner it has not
    got, and the approve button would then refuse."""
    t = org_project
    _, hdr = await _member(t, "ba", ["run:create", "artifact:view"])
    with TestClient(process_api.app) as c:
        _upload(c, t, hdr, filename="policy.pdf")
        _upload(c, t, hdr, filename="brd.pdf", stage="requirements")

        scoped = c.get(f"/projects/{t['project']}/artifacts?phase=requirements",
                       headers=hdr).json()
        everything = c.get(f"/projects/{t['project']}/artifacts", headers=hdr).json()

    titles = {a["title"] for a in scoped if a["type"] == "document"}
    assert "brd.pdf" in titles and "policy.pdf" not in titles
    assert {"brd.pdf", "policy.pdf"} <= {
        a["title"] for a in everything if a["type"] == "document"
    }


async def test_a_run_less_document_is_still_listed_and_readable(org_project):
    """The listing used to join Run to learn a document's project, so a document
    without a run was invisible — which since 0052 is every uploaded one."""
    t = org_project
    _, hdr = await _member(t, "ba", ["run:create", "artifact:view"])
    with TestClient(process_api.app) as c:
        created = _upload(c, t, hdr, stage="requirements").json()
        listed = c.get(f"/projects/{t['project']}/artifacts", headers=hdr).json()
        one = c.get(f"/artifacts/{created['id']}", headers=hdr)
    assert created["id"] in {a["id"] for a in listed}
    assert one.status_code == 200 and one.json()["runId"] is None


# -- phase 2: who may decide --------------------------------------------------


async def test_the_stage_owner_can_approve_without_being_a_project_admin(org_project):
    """THE POINT OF PHASE 2. `ba` owns Requirements and is not a project admin; before
    this it could not accept its own stage's documents."""
    t = org_project
    uploader, up_hdr = await _member(t, "developer", ["run:create", "artifact:view"])
    _, ba_hdr = await _member(
        t, "ba", ["approve", "artifact:approve_requirements", "artifact:view"])

    with TestClient(process_api.app) as c:
        doc = _upload(c, t, up_hdr, stage="requirements").json()
        r = c.post(f"/artifacts/{doc['id']}/approve", headers=ba_hdr)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "approved"
    assert r.json()["approvedBy"], "the approver must be recorded and surfaced"
    assert r.json()["approvedAt"]


async def test_an_owner_of_another_stage_is_refused(org_project):
    """Permissions are per stage so an Architect cannot accept a Requirements
    document. Same user, same route — only the stage differs."""
    t = org_project
    _, up_hdr = await _member(t, "developer", ["run:create", "artifact:view"])
    _, arch_hdr = await _member(
        t, "architect", ["approve", "artifact:approve_design", "artifact:view"])

    with TestClient(process_api.app) as c:
        req_doc = _upload(c, t, up_hdr, stage="requirements", filename="brd.pdf").json()
        des_doc = _upload(c, t, up_hdr, stage="design", filename="hld.pdf").json()
        refused = c.post(f"/artifacts/{req_doc['id']}/approve", headers=arch_hdr)
        allowed = c.post(f"/artifacts/{des_doc['id']}/approve", headers=arch_hdr)

    assert refused.status_code in (403, 404), refused.text
    assert allowed.status_code == 200, allowed.text


async def test_a_project_level_document_needs_project_administration(org_project):
    """It has no owning role, so there is no stage permission to check. A stage owner
    must NOT be able to accept a project-wide policy on the strength of owning one
    agent."""
    t = org_project
    _, up_hdr = await _member(t, "developer", ["run:create", "artifact:view"])
    _, ba_hdr = await _member(
        t, "ba", ["approve", "artifact:approve_requirements", "artifact:view"])
    _, pa_hdr = await _member(
        t, "project_admin", ["approve", "artifact:view", "member:manage"])

    with TestClient(process_api.app) as c:
        doc = _upload(c, t, up_hdr, filename="policy.pdf").json()
        refused = c.post(f"/artifacts/{doc['id']}/approve", headers=ba_hdr)
        allowed = c.post(f"/artifacts/{doc['id']}/approve", headers=pa_hdr)

    assert refused.status_code in (403, 404), refused.text
    assert allowed.status_code == 200, allowed.text


async def test_holding_the_permission_elsewhere_does_not_reach_this_project(org_project):
    """`artifact:approve_requirements` is held tenant-wide. Without a project check,
    any BA anywhere could accept another team's documents — which is what the old
    blanket administration check was really guarding."""
    t = org_project
    _, up_hdr = await _member(t, "developer", ["run:create", "artifact:view"])
    outsider = f"outsider-{_uuid.uuid4()}"          # no binding on this project
    out_hdr = _hdr(outsider, t["org"],
                   ["approve", "artifact:approve_requirements", "artifact:view"])

    with TestClient(process_api.app) as c:
        doc = _upload(c, t, up_hdr, stage="requirements").json()
        r = c.post(f"/artifacts/{doc['id']}/approve", headers=out_hdr)
    assert r.status_code in (403, 404), r.text


# -- the note the uploader leaves for the approver ----------------------------


async def test_an_uploaders_note_reaches_the_response(org_project):
    """WHY THIS IS BEING PUT FORWARD. An approver had a filename, a stage and a name,
    and no answer to "why am I being asked to accept this" — that lived only in
    whatever conversation happened around the upload."""
    t = org_project
    _, hdr = await _member(t, "ba", ["run:create", "artifact:view"])
    with TestClient(process_api.app) as c:
        r = _upload(c, t, hdr, stage="requirements",
                    note="Replaces the draft Ana rejected; only section 3 changed.")

    assert r.json()["uploadNote"] == "Replaces the draft Ana rejected; only section 3 changed."


async def test_a_document_without_a_note_has_none(org_project):
    """NON-VACUITY, and the rule: the note is OPTIONAL. A required box gets '.' typed
    into it, and a meaningless note is worse than none because the approver still has
    to read it."""
    t = org_project
    _, hdr = await _member(t, "ba", ["run:create", "artifact:view"])
    with TestClient(process_api.app) as c:
        r = _upload(c, t, hdr, stage="requirements")

    assert r.json()["uploadNote"] is None


async def test_a_blank_note_is_stored_as_no_note(org_project):
    """One empty case, not two. A whitespace-only note would otherwise render as an
    empty pair of quotation marks under the document."""
    t = org_project
    _, hdr = await _member(t, "ba", ["run:create", "artifact:view"])
    with TestClient(process_api.app) as c:
        r = _upload(c, t, hdr, stage="requirements", note="   ")

    assert r.json()["uploadNote"] is None


async def test_an_over_long_note_is_truncated_rather_than_refused(org_project):
    """THE FILE MATTERS MORE THAN THE SENTENCE. Enforcing the length by rejecting the
    request would lose the upload over a long note, so it is capped and the document
    still lands."""
    t = org_project
    _, hdr = await _member(t, "ba", ["run:create", "artifact:view"])
    with TestClient(process_api.app) as c:
        r = _upload(c, t, hdr, stage="requirements", note="x" * 3000)

    assert r.status_code == 200, r.text
    assert len(r.json()["uploadNote"]) == 2000
