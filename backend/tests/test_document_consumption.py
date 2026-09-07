"""One agent reading another's documents — phase 6.

THE RULE, and every row of it is asserted directly below:

    project-level, approved                every agent
    agent-level, COVERED by a published    every agent
      version
    agent-level, approved but not covered  its own agent only
    pending or rejected                    nobody

APPROVED AND COVERED ARE DIFFERENT QUESTIONS. Approved means "fit to exist in the
project's record". Covered means "part of the signed-off unit this stage handed
downstream". A document can be a perfectly good document and still not be something
another agent should build on, and collapsing the two would make the gate decorative.

WHY `covers` RATHER THAN A SECOND APPROVAL SYSTEM. `artifact_versions.covers` was added
for exactly this and never populated. Documents joining the existing gate means one
audit trail and one thing called approval, instead of two that have to be reconciled.
"""
from __future__ import annotations

import sys
import uuid as _uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.db import get_db_session_superuser  # noqa: E402
from shared.services.artifact_versions import (  # noqa: E402
    publish_version, readable_documents, snapshot_stage_payload,
)

pytestmark = pytest.mark.usefixtures("purge_created_orgs")

PRODUCER = "producer@example.com"
OWNER = "owner@example.com"


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
            "INSERT INTO organizations (id, slug, display_name) "
            "VALUES (:i, :s, 'Doc Consumption Test')"
        ), {"i": org, "s": f"doc-{org[:8]}"})
        await s.execute(text(
            "INSERT INTO workspaces (id, organization_id, slug, display_name) "
            "VALUES (:i, :o, 'unit', 'Unit')"
        ), {"i": bu, "o": org})
        await s.execute(
            text("SELECT set_config('app.current_tenant_id', :t, true)"), {"t": org})
        await s.execute(text(
            "INSERT INTO projects (id, workspace_id, tenant_id, display_name, "
            "  enforce_artifact_publication) "
            "VALUES (:i, :w, :t, 'Doc Project', true)"
        ), {"i": proj, "w": bu, "t": org})
        await s.commit()
    yield {"org": org, "bu": bu, "project": proj}


async def _session(tenant_id: str):
    ctx = get_db_session_superuser()
    db = await ctx.__aenter__()
    await db.execute(
        text("SELECT set_config('app.current_tenant_id', :t, true)"), {"t": tenant_id})
    return ctx, db


async def _close(ctx, db):
    """Release WITHOUT committing — see test_artifact_versions.py::_close."""
    try:
        await db.rollback()
    finally:
        await ctx.__aexit__(None, None, None)


async def _document(project, *, stage, status="approved", name="doc.pdf") -> str:
    """A document row. Returns its id."""
    art = str(_uuid.uuid4())
    async with get_db_session_superuser() as s:
        await s.execute(
            text("SELECT set_config('app.current_tenant_id', :t, true)"),
            {"t": project["org"]})
        # `approved_at` computed here rather than with a second `:s` in a CASE —
        # reusing one bind in two positions makes asyncpg deduce two different types
        # for it and refuse the statement.
        approved = status == "approved"
        await s.execute(text(
            "INSERT INTO artifacts (id, project_id, stage, tenant_id, artifact_type, "
            "  blob_path, approval_status, approved_by, approved_at, uploaded_by) "
            "VALUES (CAST(:i AS uuid), CAST(:p AS uuid), :st, CAST(:t AS uuid), "
            "  'document', :bp, :s, :ab, :at, :ub)"
        ), {"i": art, "p": project["project"], "st": stage, "t": project["org"],
            "bp": f"{project['org']}/bu/proj/{stage or '_no-agent'}/x/document/{name}",
            "s": status, "ab": OWNER if approved else None,
            "at": datetime.now(timezone.utc) if approved else None,
            "ub": PRODUCER})
        await s.commit()
    return art


async def _publish(project, stage, payload, covers=None) -> int:
    async with get_db_session_superuser() as s:
        await s.execute(
            text("SELECT set_config('app.current_tenant_id', :t, true)"),
            {"t": project["org"]})
        ref = await snapshot_stage_payload(
            s, tenant_id=project["org"], project_id=project["project"], stage=stage,
            payload=payload, produced_by=PRODUCER, covers=covers or [])
        await publish_version(
            s, tenant_id=project["org"], project_id=project["project"], stage=stage,
            version=ref.version, published_by=OWNER)
        await s.commit()
    return ref.version


async def _readable(project, covered_ids=None):
    ctx, db = await _session(project["org"])
    try:
        return await readable_documents(
            db, project["project"], covered_ids=covered_ids or [])
    finally:
        await _close(ctx, db)


# -- the four rows of the rule ------------------------------------------------


async def test_an_approved_project_level_document_is_readable_by_everyone(project):
    """A project-wide policy IS context for every agent by definition."""
    doc = await _document(project, stage=None, name="policy.pdf")
    docs = await _readable(project)
    assert [d["id"] for d in docs] == [doc]
    assert docs[0]["scope"] == "project" and docs[0]["via"] == "project"


async def test_a_covered_agent_document_is_readable_by_everyone(project):
    doc = await _document(project, stage="design", name="hld.pdf")
    await _publish(project, "design", {"c4": "x"}, covers=[doc])

    docs = await _readable(project, covered_ids=[doc])
    assert [d["id"] for d in docs] == [doc]
    assert docs[0]["scope"] == "agent" and docs[0]["via"] == "covered"


async def test_an_uncovered_agent_document_is_not_offered(project):
    """THE DISTINCTION THE GATE EXISTS FOR. Approved is not the same as handed
    downstream — a stage's own working document stays its own."""
    await _document(project, stage="design", name="scratch.pdf")
    await _publish(project, "design", {"c4": "x"}, covers=[])

    assert await _readable(project, covered_ids=[]) == []


@pytest.mark.parametrize("status", ["pending", "rejected"])
async def test_an_unapproved_document_is_never_offered(project, status):
    """Not even when a version names it. Covering is not approving, and a document
    rejected after being frozen into `covers` must stop being readable — the frozen
    version still names it, so the status has to be re-checked at read time."""
    doc = await _document(project, stage="design", status=status, name="draft.pdf")
    await _publish(project, "design", {"c4": "x"}, covers=[doc])

    assert await _readable(project, covered_ids=[doc]) == []


# -- what the metadata says ---------------------------------------------------


async def test_the_metadata_names_the_approver_and_not_the_bytes(project):
    """`who approved this` is the question the screen and the audit both ask. A blob
    path is NOT included: an agent should not be reasoning about storage locations, and
    handing one over would be a second, unguarded way to the file."""
    doc = await _document(project, stage=None, name="policy.pdf")
    d = (await _readable(project))[0]

    assert d["approvedBy"] == OWNER and d["approvedAt"]
    assert d["title"] == "policy.pdf"
    assert "blob" not in str(d).lower() and "path" not in d


async def test_both_kinds_are_listed_together(project):
    """A consumer sees the project-wide documents AND the covered ones in one answer;
    two calls would let a caller forget the second."""
    project_doc = await _document(project, stage=None, name="policy.pdf")
    design_doc = await _document(project, stage="design", name="hld.pdf")
    await _publish(project, "design", {"c4": "x"}, covers=[design_doc])

    docs = await _readable(project, covered_ids=[design_doc])
    assert {d["id"] for d in docs} == {project_doc, design_doc}
    assert {d["via"] for d in docs} == {"project", "covered"}


async def test_documents_are_tenant_isolated(project):
    await _document(project, stage=None, name="policy.pdf")
    ctx, db = await _session(str(_uuid.uuid4()))
    try:
        assert await readable_documents(db, project["project"]) == []
    finally:
        await _close(ctx, db)


# -- read_upstream carries them ------------------------------------------------


async def test_read_upstream_returns_the_covered_documents(project):
    """The whole point: PM asks for design and gets the payload AND the documents that
    were signed off with it."""
    from shared.services.artifact_consumption import read_upstream_for_agent

    doc = await _document(project, stage="design", name="hld.pdf")
    await _publish(project, "design", {"c4": "signed"}, covers=[doc])

    async def _never():
        raise AssertionError("the legacy reader must not run under enforcement")

    result = await read_upstream_for_agent(
        tenant_id=project["org"], project_id=project["project"], stage="design",
        consumer_stage="plan", legacy_reader=lambda: _never())

    assert result.payload == {"c4": "signed"}
    assert [d["id"] for d in result.documents] == [doc]


async def test_an_unenforced_project_does_not_claim_documents_are_approved(project):
    """With the flag off the legacy payload comes back and NOTHING is asserted about
    approval — listing documents there would imply a gate that is not switched on."""
    from shared.services.artifact_consumption import read_upstream_for_agent

    async with get_db_session_superuser() as s:
        await s.execute(
            text("SELECT set_config('app.current_tenant_id', :t, true)"),
            {"t": project["org"]})
        await s.execute(text(
            "UPDATE projects SET enforce_artifact_publication = false "
            "WHERE id = CAST(:p AS uuid)"), {"p": project["project"]})
        await s.commit()

    await _document(project, stage=None, name="policy.pdf")
    result = await read_upstream_for_agent(
        tenant_id=project["org"], project_id=project["project"], stage="design",
        consumer_stage="plan", legacy_reader=lambda: _draft())

    assert result.unenforced is True
    assert result.documents == []


async def _draft():
    return {"c4": "the working draft"}


# -- the evidence trail can now hold a document read ---------------------------


async def test_a_document_read_can_be_recorded_without_a_version(project):
    """`version_id` was NOT NULL, so a project-level document read — which has no
    version to point at — could not be recorded at all. An evidence view with a hole in
    it is worse than none: it reads as complete."""
    from shared.services.artifact_versions import record_document_consumption

    doc = await _document(project, stage=None, name="policy.pdf")
    ctx, db = await _session(project["org"])
    try:
        await record_document_consumption(
            db, tenant_id=project["org"], project_id=project["project"],
            artifact_id=doc, producing_stage=None, consumer_stage="plan",
            consumed_by="pm@example.com")
        await db.flush()
        row = (await db.execute(text(
            "SELECT artifact_id::text, version_id, consumer_stage FROM "
            "artifact_consumptions WHERE project_id = CAST(:p AS uuid)"
        ), {"p": project["project"]})).fetchone()
        assert row is not None
        assert row[0] == doc and row[1] is None and row[2] == "plan"
    finally:
        await _close(ctx, db)


async def test_a_consumption_row_must_point_at_something(project):
    """The CHECK that stops a row recording nothing at all."""
    from sqlalchemy.exc import IntegrityError

    ctx, db = await _session(project["org"])
    try:
        with pytest.raises(IntegrityError):
            await db.execute(text(
                "INSERT INTO artifact_consumptions "
                "  (tenant_id, project_id, consumer_stage) "
                "VALUES (CAST(:t AS uuid), CAST(:p AS uuid), 'plan')"
            ), {"t": project["org"], "p": project["project"]})
            await db.flush()
    finally:
        await _close(ctx, db)
