"""What a run built on, and what was built on a version — phase 7.

THE QUESTION AN AUDITOR ASKS, and before phase 3 it had no answer at all. Every
consumer took the latest non-null payload ordered by `created_at desc`, so nothing
recorded that a particular run read a particular thing. Not a stale answer: none.

TWO DIRECTIONS, and they are asked at different moments:

  run -> versions   "what did this deployment build on", asked when it goes wrong
  version -> runs   "what was built on this design", asked when the design does

THE CONTENT HASH is what makes this evidence rather than a note. "Read design v2" is a
claim about a row; "read the payload hashing to 5041bf" is checkable against the audit
entry written when that version was signed.

AND AN EXCEPTION MUST LOOK LIKE ONE. A grant listed identically to routine approved
work is how a reviewer learns the column means nothing.
"""
from __future__ import annotations

import sys
import uuid as _uuid
from pathlib import Path

import pytest
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.db import get_db_session_superuser  # noqa: E402
from shared.services.artifact_consumption import read_upstream_for_agent  # noqa: E402
from shared.services.artifact_versions import (  # noqa: E402
    canonical_hash, publish_version, run_evidence, snapshot_stage_payload,
    version_consumers,
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
            "VALUES (:i, :s, 'Artifact Evidence Test')"
        ), {"i": org, "s": f"evid-{org[:8]}"})
        await s.execute(text(
            "INSERT INTO workspaces (id, organization_id, slug, display_name) "
            "VALUES (:i, :o, 'unit', 'Unit')"
        ), {"i": bu, "o": org})
        await s.execute(
            text("SELECT set_config('app.current_tenant_id', :t, true)"), {"t": org})
        await s.execute(text(
            "INSERT INTO projects (id, workspace_id, tenant_id, display_name, "
            "  enforce_artifact_publication) "
            "VALUES (:i, :w, :t, 'Evidence Project', true)"
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


async def _never():
    raise AssertionError("the legacy reader must not run under enforcement")


async def _publish(project, stage: str, payload: dict) -> int:
    async with get_db_session_superuser() as s:
        await s.execute(
            text("SELECT set_config('app.current_tenant_id', :t, true)"),
            {"t": project["org"]})
        ref = await snapshot_stage_payload(
            s, tenant_id=project["org"], project_id=project["project"], stage=stage,
            payload=payload, produced_by=PRODUCER)
        await publish_version(
            s, tenant_id=project["org"], project_id=project["project"], stage=stage,
            version=ref.version, published_by=OWNER)
        await s.commit()
    return ref.version


async def _draft(project, stage: str, payload: dict) -> str:
    async with get_db_session_superuser() as s:
        await s.execute(
            text("SELECT set_config('app.current_tenant_id', :t, true)"),
            {"t": project["org"]})
        ref = await snapshot_stage_payload(
            s, tenant_id=project["org"], project_id=project["project"], stage=stage,
            payload=payload, produced_by=PRODUCER)
        await s.commit()
    return ref.id


async def _grant(project, version_id: str, consumer_stage: str):
    async with get_db_session_superuser() as s:
        await s.execute(
            text("SELECT set_config('app.current_tenant_id', :t, true)"),
            {"t": project["org"]})
        await s.execute(text(
            "INSERT INTO artifact_consumption_grants "
            "  (tenant_id, project_id, version_id, consumer_stage, granted_by, reason) "
            "VALUES (CAST(:t AS uuid), CAST(:p AS uuid), CAST(:v AS uuid), :c, :g, :r)"
        ), {"t": project["org"], "p": project["project"], "v": version_id,
            "c": consumer_stage, "g": OWNER, "r": "needed for a spike"})
        await s.commit()


async def _run_row(project) -> str:
    run_id = str(_uuid.uuid4())
    async with get_db_session_superuser() as s:
        await s.execute(
            text("SELECT set_config('app.current_tenant_id', :t, true)"),
            {"t": project["org"]})
        await s.execute(text(
            "INSERT INTO runs (id, tenant_id, project_id, stage) "
            "VALUES (CAST(:i AS uuid), CAST(:t AS uuid), CAST(:p AS uuid), 'deployment')"
        ), {"i": run_id, "t": project["org"], "p": project["project"]})
        await s.commit()
    return run_id


async def _read(project, stage: str, consumer: str, run: str | None = None):
    await read_upstream_for_agent(
        tenant_id=project["org"], project_id=project["project"], stage=stage,
        consumer_stage=consumer, consumer_run_id=run, legacy_reader=lambda: _never())


# -- what did this run build on -----------------------------------------------


async def test_a_run_can_say_what_it_built_on(project):
    await _publish(project, "design", {"c4": "signed"})
    await _publish(project, "testing", {"suites": 3})
    run = await _run_row(project)
    await _read(project, "design", "deployment", run)
    await _read(project, "testing", "deployment", run)

    ctx, db = await _session(project["org"])
    try:
        rows = await run_evidence(db, run)
        assert [r["producingStage"] for r in rows] == ["design", "testing"]
        assert all(r["consumerStage"] == "deployment" for r in rows)
    finally:
        await _close(ctx, db)


async def test_the_evidence_carries_the_content_hash(project):
    """What makes it evidence rather than a note — checkable against the audit entry
    written when the version was signed."""
    await _publish(project, "design", {"c4": "signed"})
    run = await _run_row(project)
    await _read(project, "design", "deployment", run)

    ctx, db = await _session(project["org"])
    try:
        row = (await run_evidence(db, run))[0]
        assert row["contentHash"] == canonical_hash({"c4": "signed"})
        assert row["publishedBy"] == OWNER
        assert row["producedBy"] == PRODUCER
    finally:
        await _close(ctx, db)


async def test_an_exception_is_distinguishable_from_routine_approved_work(project):
    """THE ONE THAT MATTERS HERE. `via_grant` existed on the read but was never
    persisted, so the distinction survived exactly as long as the request that made
    it — which is no use at all to somebody reading this a month later."""
    vid = await _draft(project, "design", {"c4": "unapproved"})
    await _grant(project, vid, "deployment")
    await _publish(project, "testing", {"suites": 3})
    run = await _run_row(project)
    await _read(project, "design", "deployment", run)
    await _read(project, "testing", "deployment", run)

    ctx, db = await _session(project["org"])
    try:
        by_stage = {r["producingStage"]: r for r in await run_evidence(db, run)}
        assert by_stage["design"]["viaGrant"] is True
        assert by_stage["testing"]["viaGrant"] is False
    finally:
        await _close(ctx, db)


async def test_the_evidence_shows_a_version_since_superseded(project):
    """Exactly the case somebody goes looking for: the run stays correct about what it
    read, AND the view says the ground has moved."""
    await _publish(project, "design", {"c4": "v1"})
    run = await _run_row(project)
    await _read(project, "design", "deployment", run)
    await _publish(project, "design", {"c4": "v2"})

    ctx, db = await _session(project["org"])
    try:
        row = (await run_evidence(db, run))[0]
        assert row["version"] == 1, "history must not be rewritten"
        assert row["statusNow"] == "superseded"
    finally:
        await _close(ctx, db)


async def test_repeated_reads_are_not_collapsed(project):
    """An agent that read its upstream twice did so. Tidying that up would edit
    history to look neater than it was."""
    await _publish(project, "design", {"c4": "signed"})
    run = await _run_row(project)
    await _read(project, "design", "deployment", run)
    await _read(project, "design", "deployment", run)

    ctx, db = await _session(project["org"])
    try:
        assert len(await run_evidence(db, run)) == 2
    finally:
        await _close(ctx, db)


async def test_a_run_that_consumed_nothing_reads_as_empty_not_as_an_error(project):
    """Empty is meaningful: no upstream, or enforcement was off when it ran. The UI
    has to say which rather than rendering blank."""
    run = await _run_row(project)
    ctx, db = await _session(project["org"])
    try:
        assert await run_evidence(db, run) == []
    finally:
        await _close(ctx, db)


# -- what was built on this version -------------------------------------------


async def test_a_version_can_name_everything_built_on_it(project):
    """The blast radius: after a design turns out to be wrong, what has to be looked
    at again."""
    await _publish(project, "design", {"c4": "signed"})
    for consumer in ("security", "code_review", "deployment"):
        await _read(project, "design", consumer)

    ctx, db = await _session(project["org"])
    try:
        rows = await version_consumers(db, project["project"], "design", 1)
        assert sorted(r["consumerStage"] for r in rows) == [
            "code_review", "deployment", "security"]
    finally:
        await _close(ctx, db)


async def test_the_blast_radius_is_per_version_not_per_stage(project):
    """v1's consumers are not v2's. Conflating them would over-report every time a
    stage republishes, and an over-broad blast radius is ignored like any other."""
    await _publish(project, "design", {"c4": "v1"})
    await _read(project, "design", "security")
    await _publish(project, "design", {"c4": "v2"})
    await _read(project, "design", "code_review")

    ctx, db = await _session(project["org"])
    try:
        v1 = await version_consumers(db, project["project"], "design", 1)
        v2 = await version_consumers(db, project["project"], "design", 2)
        assert [r["consumerStage"] for r in v1] == ["security"]
        assert [r["consumerStage"] for r in v2] == ["code_review"]
    finally:
        await _close(ctx, db)


async def test_a_version_nobody_read_has_an_empty_blast_radius(project):
    await _publish(project, "design", {"c4": "signed"})
    ctx, db = await _session(project["org"])
    try:
        assert await version_consumers(db, project["project"], "design", 1) == []
    finally:
        await _close(ctx, db)


# -- isolation -----------------------------------------------------------------


async def test_evidence_is_tenant_isolated(project):
    await _publish(project, "design", {"c4": "signed"})
    run = await _run_row(project)
    await _read(project, "design", "deployment", run)

    ctx, db = await _session(str(_uuid.uuid4()))
    try:
        assert await run_evidence(db, run) == []
        assert await version_consumers(db, project["project"], "design", 1) == []
    finally:
        await _close(ctx, db)
