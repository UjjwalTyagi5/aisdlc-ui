"""Consuming another agent's work — phase 3.

THE BEHAVIOUR CHANGE. Four tools each took the latest non-null `runs.{stage}_artifacts`
ordered by `created_at desc`, having never asked whether a human accepted it. They now
go through one helper that reads published versions only.

THE FLAG IS THE SAFETY, AND IT IS THE FIRST THING TESTED. Every project defaults to
`enforce_artifact_publication = false`, and with it off the behaviour must be
byte-for-byte what it was. A regression there breaks every agent on every project,
which is a far bigger blast radius than the feature itself.

THE FALLBACK THAT MUST NOT EXIST. With enforcement on and nothing published, the answer
is "no approved design exists yet" — never the draft. A fallback would make the gate
decorative in exactly the way the tenant-wide credential fallback made "Needs a
credential" decorative until it was removed: the feature would look like it worked
while never once refusing anything.
"""
from __future__ import annotations

import sys
import uuid as _uuid
from pathlib import Path

import pytest
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.db import get_db_session_superuser  # noqa: E402
from shared.services.artifact_consumption import (  # noqa: E402
    describe, read_upstream_for_agent,
)
from shared.services.artifact_versions import (  # noqa: E402
    consumptions_for_run, enforcement_enabled, publish_version, read_upstream,
    snapshot_stage_payload,
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
            "VALUES (:i, :s, 'Artifact Consumption Test')"
        ), {"i": org, "s": f"cons-{org[:8]}"})
        await s.execute(text(
            "INSERT INTO workspaces (id, organization_id, slug, display_name) "
            "VALUES (:i, :o, 'unit', 'Unit')"
        ), {"i": bu, "o": org})
        await s.execute(
            text("SELECT set_config('app.current_tenant_id', :t, true)"), {"t": org})
        await s.execute(text(
            "INSERT INTO projects (id, workspace_id, tenant_id, display_name) "
            "VALUES (:i, :w, :t, 'Consumption Project')"
        ), {"i": proj, "w": bu, "t": org})
        await s.commit()
    yield {"org": org, "bu": bu, "project": proj}


async def _session(tenant_id: str):
    ctx = get_db_session_superuser()
    db = await ctx.__aenter__()
    await db.execute(
        text("SELECT set_config('app.current_tenant_id', :t, true)"), {"t": tenant_id})
    return ctx, db


async def _set_enforcement(project: dict, on: bool):
    async with get_db_session_superuser() as s:
        await s.execute(
            text("SELECT set_config('app.current_tenant_id', :t, true)"),
            {"t": project["org"]})
        await s.execute(text(
            "UPDATE projects SET enforce_artifact_publication = :v "
            "WHERE id = CAST(:p AS uuid)"
        ), {"v": on, "p": project["project"]})
        await s.commit()


async def _publish(project: dict, stage: str, payload: dict) -> int:
    """A published version of `stage`, via the real snapshot + publish path."""
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


async def _close(ctx, db):
    """Release the session WITHOUT committing.

    THE FLAKINESS THIS FIXES. `get_db_session_superuser` commits on a clean exit, and
    `__aexit__(None, None, None)` is a clean exit. Several tests here deliberately
    trigger CHECK-constraint and trigger violations, which leave the transaction
    aborted — committing that returns a poisoned connection to the pool, and the NEXT
    test's transaction-local `set_config('app.current_tenant_id')` never takes effect,
    so its INSERT fails the RLS policy. The failure lands in an unrelated test, which
    is why it read as random.

    Rolling back first also stops these tests persisting rows they never meant to keep.
    """
    try:
        await db.rollback()
    finally:
        await ctx.__aexit__(None, None, None)


# -- the flag -----------------------------------------------------------------


async def test_projects_do_not_enforce_by_default(project):
    """Every existing project lands on this. If the default were true, the migration
    itself would stop every agent in the estate."""
    ctx, db = await _session(project["org"])
    try:
        assert await enforcement_enabled(db, project["project"]) is False
    finally:
        await _close(ctx, db)


async def test_a_missing_project_does_not_enforce(project):
    """FAILS TOWARDS TODAY'S BEHAVIOUR, not towards enforcement. A lookup that fails
    turning enforcement ON would halt every agent with a message that looks like an
    outage — a worse failure than continuing to do what it did yesterday."""
    ctx, db = await _session(project["org"])
    try:
        assert await enforcement_enabled(db, str(_uuid.uuid4())) is False
    finally:
        await _close(ctx, db)


async def test_with_enforcement_off_the_legacy_reader_is_used(project):
    """The blast-radius test. Off means byte-for-byte the old behaviour."""
    called = []

    async def legacy():
        called.append(True)
        return {"from": "the working draft"}

    result = await read_upstream_for_agent(
        tenant_id=project["org"], project_id=project["project"], stage="design",
        consumer_stage="security", legacy_reader=legacy)

    assert called == [True]
    assert result.payload == {"from": "the working draft"}
    assert result.unenforced is True


async def test_with_enforcement_on_the_legacy_reader_is_never_called(project):
    """THE ONE THAT WOULD HIDE A BROKEN GATE. If the legacy path still ran, an
    unpublished draft could reach a consumer and every other test here would still
    pass."""
    await _set_enforcement(project, True)
    called = []

    async def legacy():
        called.append(True)
        return {"from": "the working draft"}

    result = await read_upstream_for_agent(
        tenant_id=project["org"], project_id=project["project"], stage="design",
        consumer_stage="security", legacy_reader=legacy)

    assert called == [], "the draft path ran while enforcement was on"
    assert result.payload is None
    assert result.unenforced is False


# -- no fallback --------------------------------------------------------------


async def test_nothing_published_returns_none_with_a_reason(project):
    await _set_enforcement(project, True)

    async def legacy():
        return {"draft": True}

    result = await read_upstream_for_agent(
        tenant_id=project["org"], project_id=project["project"], stage="design",
        consumer_stage="security", legacy_reader=legacy)

    assert not result.found
    assert "No approved design" in (result.reason or "")
    # The sentence a tool actually returns has to say this is not a malfunction, or an
    # agent goes looking for a bug that is not there.
    assert "not an error" in describe(result)


async def test_an_unpublished_draft_is_not_returned(project):
    """A version EXISTS; nobody signed it. That must read the same as nothing."""
    await _set_enforcement(project, True)
    async with get_db_session_superuser() as s:
        await s.execute(
            text("SELECT set_config('app.current_tenant_id', :t, true)"),
            {"t": project["org"]})
        await snapshot_stage_payload(
            s, tenant_id=project["org"], project_id=project["project"], stage="design",
            payload={"secret": "unapproved"}, produced_by=PRODUCER)
        await s.commit()

    result = await read_upstream_for_agent(
        tenant_id=project["org"], project_id=project["project"], stage="design",
        consumer_stage="security", legacy_reader=lambda: _never())
    assert result.payload is None
    assert "unapproved" not in str(result)


async def _never():
    raise AssertionError("the legacy reader must not run under enforcement")


async def test_a_published_version_is_returned(project):
    await _set_enforcement(project, True)
    await _publish(project, "design", {"c4": "approved"})

    result = await read_upstream_for_agent(
        tenant_id=project["org"], project_id=project["project"], stage="design",
        consumer_stage="security", legacy_reader=lambda: _never())

    assert result.payload == {"c4": "approved"}
    assert result.version == 1 and result.published_by == OWNER


async def test_a_newer_draft_does_not_shadow_the_published_version(project):
    """The consumer keeps reading v1 until v2 is signed — not switched silently."""
    await _set_enforcement(project, True)
    await _publish(project, "design", {"c4": "v1"})
    async with get_db_session_superuser() as s:
        await s.execute(
            text("SELECT set_config('app.current_tenant_id', :t, true)"),
            {"t": project["org"]})
        await snapshot_stage_payload(
            s, tenant_id=project["org"], project_id=project["project"], stage="design",
            payload={"c4": "v2 unapproved"}, produced_by=PRODUCER)
        await s.commit()

    result = await read_upstream_for_agent(
        tenant_id=project["org"], project_id=project["project"], stage="design",
        consumer_stage="security", legacy_reader=lambda: _never())
    assert result.payload == {"c4": "v1"} and result.version == 1


async def test_an_unknown_stage_returns_a_reason_rather_than_raising(project):
    """An agent should degrade, not die, on a stage name it should never have sent."""
    await _set_enforcement(project, True)
    result = await read_upstream_for_agent(
        tenant_id=project["org"], project_id=project["project"], stage="review",
        consumer_stage="security", legacy_reader=lambda: _never())
    assert not result.found and "review" in (result.reason or "")


async def test_no_project_context_is_a_reason_not_a_crash(project):
    result = await read_upstream_for_agent(
        tenant_id="", project_id="", stage="design",
        consumer_stage="security", legacy_reader=lambda: _never())
    assert not result.found and "no project context" in (result.reason or "")


async def test_a_failing_read_says_it_failed_rather_than_reporting_nothing(project):
    """The tools this replaces swallowed every error into `pass`, so a broken read and
    an absent artifact were the same thing to the agent AND to the logs."""
    async def boom():
        raise RuntimeError("database on fire")

    result = await read_upstream_for_agent(
        tenant_id=project["org"], project_id=project["project"], stage="design",
        consumer_stage="security", legacy_reader=boom)
    assert not result.found
    assert "could not read" in (result.reason or "")
    assert "RuntimeError" in (result.reason or "")


# -- the evidence trail -------------------------------------------------------


async def test_a_successful_read_is_recorded(project):
    await _set_enforcement(project, True)
    await _publish(project, "design", {"c4": "approved"})
    run_id = None

    await read_upstream_for_agent(
        tenant_id=project["org"], project_id=project["project"], stage="design",
        consumer_stage="security", legacy_reader=lambda: _never(),
        consumer_run_id=run_id, consumed_by="scanner@example.com")

    ctx, db = await _session(project["org"])
    try:
        rows = (await db.execute(text(
            "SELECT producing_stage, version, consumer_stage, consumed_by "
            "FROM artifact_consumptions WHERE project_id = CAST(:p AS uuid)"
        ), {"p": project["project"]})).fetchall()
        assert len(rows) == 1
        assert tuple(rows[0]) == ("design", 1, "security", "scanner@example.com")
    finally:
        await _close(ctx, db)


async def test_the_consumption_is_committed_not_just_flushed(project):
    """The helper owns its own session — no request-scoped commit will follow it. A
    flush without a commit records nothing while every read appears to work."""
    await _set_enforcement(project, True)
    await _publish(project, "design", {"c4": "approved"})
    await read_upstream_for_agent(
        tenant_id=project["org"], project_id=project["project"], stage="design",
        consumer_stage="security", legacy_reader=lambda: _never())

    # A brand-new connection: nothing of the previous transaction can be visible
    # unless it was actually committed.
    ctx, db = await _session(project["org"])
    try:
        n = (await db.execute(text(
            "SELECT count(*) FROM artifact_consumptions "
            "WHERE project_id = CAST(:p AS uuid)"
        ), {"p": project["project"]})).scalar()
        assert n == 1
    finally:
        await _close(ctx, db)


async def test_nothing_is_recorded_when_nothing_was_read(project):
    """A consumption row for a read that returned nothing would make the evidence
    trail claim a run built on something it never saw."""
    await _set_enforcement(project, True)
    await read_upstream_for_agent(
        tenant_id=project["org"], project_id=project["project"], stage="design",
        consumer_stage="security", legacy_reader=lambda: _never())

    ctx, db = await _session(project["org"])
    try:
        n = (await db.execute(text(
            "SELECT count(*) FROM artifact_consumptions "
            "WHERE project_id = CAST(:p AS uuid)"
        ), {"p": project["project"]})).scalar()
        assert n == 0
    finally:
        await _close(ctx, db)


async def test_the_legacy_path_records_nothing(project):
    """With enforcement off there is no version to point at, so a consumption row
    would be a fabrication."""
    await read_upstream_for_agent(
        tenant_id=project["org"], project_id=project["project"], stage="design",
        consumer_stage="security", legacy_reader=lambda: _draft())

    ctx, db = await _session(project["org"])
    try:
        n = (await db.execute(text(
            "SELECT count(*) FROM artifact_consumptions "
            "WHERE project_id = CAST(:p AS uuid)"
        ), {"p": project["project"]})).scalar()
        assert n == 0
    finally:
        await _close(ctx, db)


async def _draft():
    return {"draft": True}


async def test_repeated_reads_are_all_recorded(project):
    """Deliberately not deduplicated: an agent may read its upstream more than once in
    a turn, and collapsing those would discard the fact that it did."""
    await _set_enforcement(project, True)
    await _publish(project, "design", {"c4": "approved"})
    for _ in range(3):
        await read_upstream_for_agent(
            tenant_id=project["org"], project_id=project["project"], stage="design",
            consumer_stage="security", legacy_reader=lambda: _never())

    ctx, db = await _session(project["org"])
    try:
        n = (await db.execute(text(
            "SELECT count(*) FROM artifact_consumptions "
            "WHERE project_id = CAST(:p AS uuid)"
        ), {"p": project["project"]})).scalar()
        assert n == 3
    finally:
        await _close(ctx, db)


async def test_two_consumers_of_one_version_are_distinguishable(project):
    """"Who built on this version" needs to name each of them."""
    await _set_enforcement(project, True)
    await _publish(project, "design", {"c4": "approved"})
    for consumer in ("security", "code_review", "documentation"):
        await read_upstream_for_agent(
            tenant_id=project["org"], project_id=project["project"], stage="design",
            consumer_stage=consumer, legacy_reader=lambda: _never())

    ctx, db = await _session(project["org"])
    try:
        rows = (await db.execute(text(
            "SELECT consumer_stage FROM artifact_consumptions "
            "WHERE project_id = CAST(:p AS uuid) ORDER BY consumer_stage"
        ), {"p": project["project"]})).scalars().all()
        assert list(rows) == ["code_review", "documentation", "security"]
    finally:
        await _close(ctx, db)


# -- tenant isolation ---------------------------------------------------------


async def test_consumptions_are_tenant_isolated(project):
    await _set_enforcement(project, True)
    await _publish(project, "design", {"c4": "approved"})
    await read_upstream_for_agent(
        tenant_id=project["org"], project_id=project["project"], stage="design",
        consumer_stage="security", legacy_reader=lambda: _never())

    ctx, db = await _session(str(_uuid.uuid4()))
    try:
        n = (await db.execute(
            text("SELECT count(*) FROM artifact_consumptions"))).scalar()
        assert n == 0
    finally:
        await _close(ctx, db)


# -- read_upstream itself -----------------------------------------------------


async def test_read_upstream_can_skip_recording(project):
    """A preview ("what would this agent see") must not write evidence that a run
    consumed something it only looked at."""
    await _publish(project, "design", {"c4": "approved"})
    ctx, db = await _session(project["org"])
    try:
        result = await read_upstream(
            db, tenant_id=project["org"], project_id=project["project"],
            stage="design", consumer_stage="security", record=False)
        assert result.found
        await db.flush()
        n = (await db.execute(text(
            "SELECT count(*) FROM artifact_consumptions "
            "WHERE project_id = CAST(:p AS uuid)"
        ), {"p": project["project"]})).scalar()
        assert n == 0
    finally:
        await _close(ctx, db)


async def test_consumptions_for_run_answers_what_a_run_built_on(project):
    """The auditor's question."""
    await _set_enforcement(project, True)
    await _publish(project, "design", {"c4": "approved"})
    await _publish(project, "testing", {"suites": 3})

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

    for stage in ("design", "testing"):
        await read_upstream_for_agent(
            tenant_id=project["org"], project_id=project["project"], stage=stage,
            consumer_stage="deployment", consumer_run_id=run_id,
            legacy_reader=lambda: _never())

    ctx, db = await _session(project["org"])
    try:
        rows = await consumptions_for_run(db, run_id)
        assert sorted(r.producing_stage for r in rows) == ["design", "testing"]
        assert all(r.consumer_stage == "deployment" for r in rows)
    finally:
        await _close(ctx, db)
