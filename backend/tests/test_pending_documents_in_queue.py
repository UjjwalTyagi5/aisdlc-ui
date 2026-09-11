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


@pytest.mark.asyncio
async def test_the_queue_names_the_uploader_rather_than_their_id(project):
    """WHO PUT THIS FORWARD, as a name.

    `uploaded_by` stores `request.state.user_id` — the JWT `sub`, a UUID — so the row
    read "Waiting on BA · 09e55932-a6c1-4d8c-b6ed-7df2d013b879", answering "who is
    asking me to approve this" with a string nobody can match to a colleague.

    `shared/services/actor_labels` was written for exactly this and its docstring cites
    that very id. The Documents list already used it; this queue did not, so the same
    document read as a person in one place and as a UUID in the other.

    THE FIXTURES ELSEWHERE IN THIS FILE CANNOT CATCH THAT: they write an email into
    `uploaded_by` directly, which `actor_labels` passes through untouched, so every one
    of them would pass with the lookup removed.
    """
    uploader = str(_uuid.uuid4())
    async with get_db_session_superuser() as s:
        await s.execute(text(
            "INSERT INTO users (id, tenant_id, email, active) "
            "VALUES (:i, CAST(:t AS uuid), 'ujjwal@abcbank.com', true)"
        ), {"i": uploader, "t": project["org"]})
    await _document(project, uploaded_by=uploader)
    user = await _ba(project)

    rows = _queue(user, project, ["artifact:view", "artifact:approve_requirements"])

    assert rows[0]["requestedBy"] == "ujjwal@abcbank.com"

    async with get_db_session_superuser() as s:
        await s.execute(text("DELETE FROM users WHERE id = :i"), {"i": uploader})


@pytest.mark.asyncio
async def test_an_unresolvable_uploader_still_shows_something(project):
    """NON-VACUITY, and the fail-safe. A departed user whose row is gone must still
    render as the id — blanking it would make the request look like nobody raised it,
    which is a worse answer than an unfamiliar string."""
    ghost = str(_uuid.uuid4())
    await _document(project, uploaded_by=ghost)
    user = await _ba(project)

    rows = _queue(user, project, ["artifact:view", "artifact:approve_requirements"])

    assert rows[0]["requestedBy"] == ghost


@pytest.mark.asyncio
async def test_the_queue_carries_the_uploaders_note(project):
    """THE APPROVER'S FIRST QUESTION, answered in the row rather than on another page.

    A queue that shows a filename and a name and makes somebody navigate to find out
    why they are being asked is a queue people skip.
    """
    await _document(project, note="Replaces the draft Ana rejected; section 3 only.")
    user = await _ba(project)

    rows = _queue(user, project, ["artifact:view", "artifact:approve_requirements"])

    assert rows[0]["note"] == "Replaces the draft Ana rejected; section 3 only."


@pytest.mark.asyncio
async def test_a_document_with_no_note_says_nothing(project):
    """NON-VACUITY, and the reason the field is nullable: an optional note that
    rendered as an empty pair of quotation marks would be worse than no field."""
    await _document(project)
    user = await _ba(project)

    rows = _queue(user, project, ["artifact:view", "artifact:approve_requirements"])

    assert rows[0]["note"] is None


@pytest.mark.asyncio
async def test_the_agent_path_can_carry_a_note_too(project):
    """THE AGENT UPLOADS AND RAISES REQUESTS AS WELL, so the reason has to travel that
    path or it only works for documents a person happened to upload by hand.

    Asserted on the source: `register_generated_file` swallows its own failures by
    design (a generated document is not un-generated by a bookkeeping error), so a run
    that never reached `store_artifact` would look identical to one that did.
    """
    import inspect

    from shared.services.chat_artifacts import register_generated_file

    assert "note" in inspect.signature(register_generated_file).parameters
    src = inspect.getsource(register_generated_file)
    assert "upload_note=" in src, "the note is accepted but never stored"


# -- drafts wait on nobody -----------------------------------------------------


@pytest.mark.asyncio
async def test_a_draft_is_not_in_anyones_queue(project):
    """THE NOISE THIS REMOVES. An agent records every file it produces — ask for three
    passes at a document and two are dead the moment the third exists. Sending all three
    to an approver is how a queue stops being read, so a draft is recorded and waits on
    nobody until somebody puts it forward."""
    await _document(project, status="draft", name="brd-draft-1.docx")
    ba = await _ba(project)
    admin = await _project_admin(project)

    assert _queue(ba, project, ["artifact:view", "artifact:approve_requirements"]) == []
    assert _queue(admin, project, ["artifact:view", "approve"]) == []


@pytest.mark.asyncio
async def test_submitting_a_draft_puts_it_in_the_queue(project):
    """And the other half: raising it is what asks for the decision."""
    art = await _document(project, status="draft", name="brd-final.docx")
    ba = await _ba(project)
    perms = ["artifact:view", "run:create", "approve", "artifact:approve_requirements"]
    hdr = _headers(ba, project["org"], project["bu"], perms)

    r = _client().post(f"/artifacts/{art}/submit", headers=hdr)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "awaiting_approval"

    rows = _queue(ba, project, perms)
    assert [x["artifact"]["id"] for x in rows] == [art]


@pytest.mark.asyncio
async def test_raising_needs_only_the_permission_to_produce_work(project):
    """`run:create`, NOT `approve`. Asking for a decision is producing, not accepting —
    requiring the approve permission would mean only an approver could request an
    approval, which is nobody's idea of a workflow."""
    art = await _document(project, status="draft")
    dev = f"dev-{_uuid.uuid4()}"
    await grant_role(dev, project["proj"], "developer",
                     tenant_id=project["org"], scope_kind="project")

    r = _client().post(
        f"/artifacts/{art}/submit",
        headers=_headers(dev, project["org"], project["bu"],
                         ["artifact:view", "run:create"]))

    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_raising_twice_is_a_no_op_rather_than_an_error(project):
    """A double click is not a mistake worth a 500."""
    art = await _document(project, status="draft")
    ba = await _ba(project)
    hdr = _headers(ba, project["org"], project["bu"], ["artifact:view", "run:create"])

    assert _client().post(f"/artifacts/{art}/submit", headers=hdr).status_code == 200
    again = _client().post(f"/artifacts/{art}/submit", headers=hdr)

    assert again.status_code == 200, again.text
    assert again.json()["status"] == "awaiting_approval"


@pytest.mark.asyncio
@pytest.mark.parametrize("decided", ["approved", "rejected"])
async def test_a_decided_document_cannot_be_raised_again(project, decided):
    """"Raise for approval" must never quietly reopen a decision somebody already took —
    least of all a rejection, where re-asking is how a refused document creeps back in."""
    art = await _document(project, status=decided)
    ba = await _ba(project)

    r = _client().post(
        f"/artifacts/{art}/submit",
        headers=_headers(ba, project["org"], project["bu"],
                         ["artifact:view", "run:create"]))

    assert r.status_code == 409, r.text
    assert decided in r.json()["detail"]


def test_agent_generated_files_are_recorded_as_drafts():
    """THE DEFAULT THE WHOLE CHANGE RESTS ON, and a mutation run caught its absence:
    deleting `approval_status="draft"` from `register_generated_file` left every test in
    this file green, because they all insert their own rows and none exercised the
    writer.

    Source-level because the function is best-effort by design — it swallows its own
    failures, so a run that never reached `store_artifact` looks identical to one that
    did. `upload_artifact` is deliberately NOT covered by this: choosing a file and
    pressing Upload IS putting something forward, so a hand upload is pending.
    """
    import inspect

    from shared.services.chat_artifacts import register_generated_file

    src = inspect.getsource(register_generated_file)

    assert 'approval_status="draft"' in src, (
        "agent-generated files must start as drafts, or every working iteration lands "
        "in an approver's queue"
    )
    # Both branches: the one that uploads bytes and the one that records a row without.
    assert src.count('approval_status="draft"') >= 2, (
        "the no-bytes branch still records a pending artifact"
    )
