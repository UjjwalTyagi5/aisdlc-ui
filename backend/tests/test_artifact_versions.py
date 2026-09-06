"""Artifact versions are frozen once created — phase 1.

WHY THIS MATTERS MORE THAN IT LOOKS. Agents hand work to each other through
`runs.{stage}_artifacts`, a JSONB column written IN PLACE by
`patch_session_artifacts`. Approving that is theatre: the approval record survives,
the thing it approved does not, because the next run overwrites it. A publication gate
built on a mutable payload would produce an audit trail that looks like scrutiny and
records nothing.

So the one invariant everything later rests on is that a version cannot change. These
tests go to the REAL DATABASE on purpose. The guarantee lives in a trigger and a set
of CHECK constraints, and a fake session — the shape `test_deployment_gate.py` uses —
cannot execute either. A green suite against a mock would prove only that the mock
agrees with itself.

Each test builds its own organization, business unit and project, and
`purge_created_orgs` removes them afterwards. Nothing here touches dev data.
"""
from __future__ import annotations

import sys
import uuid as _uuid
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.db import get_db_session_superuser  # noqa: E402
from shared.services.artifact_versions import (  # noqa: E402
    ArtifactVersionError, UnknownStage, canonical_hash, assert_known_stage,
    latest_published, latest_version, list_versions, snapshot_stage_payload,
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
    """A throwaway org / business unit / project. Cleaned up by purge_created_orgs."""
    org, bu, proj = str(_uuid.uuid4()), str(_uuid.uuid4()), str(_uuid.uuid4())
    async with get_db_session_superuser() as s:
        await s.execute(text(
            "INSERT INTO organizations (id, slug, display_name) "
            "VALUES (:i, :s, 'Artifact Version Test')"
        ), {"i": org, "s": f"av-{org[:8]}"})
        await s.execute(text(
            "INSERT INTO workspaces (id, organization_id, slug, display_name) "
            "VALUES (:i, :o, 'unit', 'Unit')"
        ), {"i": bu, "o": org})
        # projects is RLS-protected; the GUC must be set before the INSERT or the
        # WITH CHECK policy refuses the row.
        await s.execute(
            text("SELECT set_config('app.current_tenant_id', :t, true)"), {"t": org})
        await s.execute(text(
            "INSERT INTO projects (id, workspace_id, tenant_id, display_name) "
            "VALUES (:i, :w, :t, 'AV Project')"
        ), {"i": proj, "w": bu, "t": org})
        await s.commit()
    yield {"org": org, "bu": bu, "project": proj}


async def _session(tenant_id: str):
    """A session with the RLS tenant set.

    `artifact_versions` has FORCE RLS on `app.current_tenant_id` and the app role is
    NOT a Postgres superuser, so a session without this reads zero rows and inserts
    are refused — silently empty, never an error.
    """
    ctx = get_db_session_superuser()
    db = await ctx.__aenter__()
    await db.execute(
        text("SELECT set_config('app.current_tenant_id', :t, true)"), {"t": tenant_id})
    return ctx, db


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


# -- hashing ------------------------------------------------------------------


def test_key_order_does_not_change_the_hash():
    """CANONICAL, or every re-run looks like a change. Version numbers that climb
    without meaning are how a reviewer learns to stop reading them."""
    assert canonical_hash({"a": 1, "b": [1, 2]}) == canonical_hash({"b": [1, 2], "a": 1})


def test_a_real_change_changes_the_hash():
    assert canonical_hash({"a": 1}) != canonical_hash({"a": 2})
    # Order inside a LIST is meaningful — only mapping key order is incidental.
    assert canonical_hash({"a": [1, 2]}) != canonical_hash({"a": [2, 1]})


def test_the_hash_does_not_raise_on_a_realistic_payload():
    """Payloads carry datetimes and UUIDs. A hash that throws on valid data is worse
    than one that stringifies it."""
    from datetime import datetime
    assert len(canonical_hash({
        "when": datetime.now(), "id": _uuid.uuid4(), "n": 1.5, "ok": True, "x": None,
    })) == 64


# -- stage validation ---------------------------------------------------------


def test_an_unknown_stage_is_refused():
    with pytest.raises(UnknownStage):
        assert_known_stage("not_a_stage")


def test_the_ui_phase_name_is_refused_not_silently_accepted():
    """`review` is the UI name; the backend stage is `code_review`. Accepting both
    would let two spellings of one stage own separate version sequences."""
    assert_known_stage("code_review")
    with pytest.raises(UnknownStage, match="code_review"):
        assert_known_stage("review")


# -- snapshotting -------------------------------------------------------------


async def test_a_snapshot_creates_version_one(project):
    ctx, db = await _session(project["org"])
    try:
        ref = await snapshot_stage_payload(
            db, tenant_id=project["org"], project_id=project["project"],
            stage="requirements", payload={"stories": [1]}, produced_by=PRODUCER)
        assert (ref.created, ref.version, ref.status) == (True, 1, "draft")
    finally:
        await _close(ctx, db)


async def test_an_identical_payload_does_not_create_a_new_version(project):
    """A stage re-running without producing anything new must not manufacture a
    version to approve."""
    ctx, db = await _session(project["org"])
    try:
        kw = dict(tenant_id=project["org"], project_id=project["project"],
                  stage="design", produced_by=PRODUCER)
        first = await snapshot_stage_payload(db, payload={"c4": "x", "adr": []}, **kw)
        again = await snapshot_stage_payload(db, payload={"adr": [], "c4": "x"}, **kw)
        assert first.created and not again.created
        assert again.version == first.version == 1
        assert again.id == first.id
    finally:
        await _close(ctx, db)


async def test_a_changed_payload_creates_the_next_version(project):
    ctx, db = await _session(project["org"])
    try:
        kw = dict(tenant_id=project["org"], project_id=project["project"],
                  stage="design", produced_by=PRODUCER)
        v1 = await snapshot_stage_payload(db, payload={"c4": "x"}, **kw)
        v2 = await snapshot_stage_payload(db, payload={"c4": "CHANGED"}, **kw)
        assert (v2.created, v2.version) == (True, 2)
        assert v2.content_hash != v1.content_hash
    finally:
        await _close(ctx, db)


async def test_version_sequences_are_per_stage(project):
    """Design v1 and Requirements v1 coexist. A shared sequence would make version
    numbers meaningless across stages."""
    ctx, db = await _session(project["org"])
    try:
        kw = dict(tenant_id=project["org"], project_id=project["project"],
                  produced_by=PRODUCER)
        a = await snapshot_stage_payload(db, stage="requirements", payload={"a": 1}, **kw)
        b = await snapshot_stage_payload(db, stage="design", payload={"b": 1}, **kw)
        assert a.version == b.version == 1
    finally:
        await _close(ctx, db)


async def test_a_snapshot_without_a_producer_is_refused(project):
    """produced_by is half of the self-publication check. An ownerless version is one
    ANY publisher could sign — the rule inverted."""
    ctx, db = await _session(project["org"])
    try:
        with pytest.raises(ArtifactVersionError, match="produced_by"):
            await snapshot_stage_payload(
                db, tenant_id=project["org"], project_id=project["project"],
                stage="requirements", payload={"a": 1}, produced_by="")
    finally:
        await _close(ctx, db)


async def test_a_null_payload_is_refused(project):
    """An empty version would look like approved work."""
    ctx, db = await _session(project["org"])
    try:
        with pytest.raises(ArtifactVersionError, match="null payload"):
            await snapshot_stage_payload(
                db, tenant_id=project["org"], project_id=project["project"],
                stage="requirements", payload=None, produced_by=PRODUCER)
    finally:
        await _close(ctx, db)


# -- THE INVARIANT ------------------------------------------------------------


async def test_the_payload_cannot_be_updated(project):
    """THE ONE THAT MATTERS. Everything later assumes a signed version cannot change.

    Enforced by the `artifact_versions_freeze` trigger rather than by convention: a
    service-layer rule is one careless UPDATE away from being false, and that failure
    would be invisible — the hash still matches its own recomputation, so a signed
    version quietly means something new.
    """
    ctx, db = await _session(project["org"])
    try:
        ref = await snapshot_stage_payload(
            db, tenant_id=project["org"], project_id=project["project"],
            stage="requirements", payload={"stories": [1]}, produced_by=PRODUCER)
        with pytest.raises((DBAPIError, IntegrityError), match="frozen"):
            await db.execute(
                text("UPDATE artifact_versions SET payload = :p WHERE id = CAST(:i AS uuid)"),
                {"p": '{"tampered": true}', "i": ref.id},
            )
            await db.flush()
    finally:
        await _close(ctx, db)


@pytest.mark.parametrize("column, value", [
    ("content_hash", "'0' || repeat('0', 63)"),
    ("version", "99"),
    ("stage", "'design'"),
    ("produced_by", "'someone.else@example.com'"),
    ("covers", "'[\"x\"]'::jsonb"),
])
async def test_identity_and_content_columns_are_all_frozen(project, column, value):
    """Not just the payload. Re-pointing a signed version at another stage, renumbering
    it, or changing which blob artifacts it covers all rewrite what was approved."""
    ctx, db = await _session(project["org"])
    try:
        ref = await snapshot_stage_payload(
            db, tenant_id=project["org"], project_id=project["project"],
            stage="requirements", payload={"stories": [1]}, produced_by=PRODUCER)
        with pytest.raises((DBAPIError, IntegrityError), match="frozen"):
            await db.execute(text(
                f"UPDATE artifact_versions SET {column} = {value} "
                "WHERE id = CAST(:i AS uuid)"
            ), {"i": ref.id})
            await db.flush()
    finally:
        await _close(ctx, db)


async def test_the_lifecycle_columns_are_NOT_frozen(project):
    """The freeze must not seize the row. draft -> published is the point of it, and a
    trigger that blocked every UPDATE would pass the test above while making the
    feature impossible."""
    ctx, db = await _session(project["org"])
    try:
        ref = await snapshot_stage_payload(
            db, tenant_id=project["org"], project_id=project["project"],
            stage="requirements", payload={"stories": [1]}, produced_by=PRODUCER)
        await db.execute(text(
            "UPDATE artifact_versions SET status='published', published_by=:o, "
            "published_at=now() WHERE id = CAST(:i AS uuid)"
        ), {"o": OWNER, "i": ref.id})
        await db.flush()
        row = await latest_published(db, project["project"], "requirements")
        assert row is not None and row.published_by == OWNER
    finally:
        await _close(ctx, db)


# -- the schema's own rules ---------------------------------------------------


async def test_self_publication_is_refused_by_the_database(project):
    """The producing agent runs AS A PERSON, so the comparison is always available.
    Matches the run gate and the deployment gate."""
    ctx, db = await _session(project["org"])
    try:
        ref = await snapshot_stage_payload(
            db, tenant_id=project["org"], project_id=project["project"],
            stage="requirements", payload={"stories": [1]}, produced_by=PRODUCER)
        with pytest.raises((DBAPIError, IntegrityError), match="no_self_publication"):
            await db.execute(text(
                "UPDATE artifact_versions SET status='published', published_by=:p, "
                "published_at=now() WHERE id = CAST(:i AS uuid)"
            ), {"p": PRODUCER, "i": ref.id})
            await db.flush()
    finally:
        await _close(ctx, db)


async def test_a_publication_must_name_its_publisher(project):
    """A version published by nobody is not published."""
    ctx, db = await _session(project["org"])
    try:
        ref = await snapshot_stage_payload(
            db, tenant_id=project["org"], project_id=project["project"],
            stage="requirements", payload={"stories": [1]}, produced_by=PRODUCER)
        with pytest.raises((DBAPIError, IntegrityError), match="published_by_someone"):
            await db.execute(text(
                "UPDATE artifact_versions SET status='published' "
                "WHERE id = CAST(:i AS uuid)"
            ), {"i": ref.id})
            await db.flush()
    finally:
        await _close(ctx, db)


async def test_a_rejection_must_say_why(project):
    """An unexplained rejection is indistinguishable from a mistake to whoever has to
    act on it."""
    ctx, db = await _session(project["org"])
    try:
        ref = await snapshot_stage_payload(
            db, tenant_id=project["org"], project_id=project["project"],
            stage="requirements", payload={"stories": [1]}, produced_by=PRODUCER)
        with pytest.raises((DBAPIError, IntegrityError), match="rejection_has_reason"):
            await db.execute(text(
                "UPDATE artifact_versions SET status='rejected' "
                "WHERE id = CAST(:i AS uuid)"
            ), {"i": ref.id})
            await db.flush()
    finally:
        await _close(ctx, db)


async def test_an_unrecognised_status_is_refused(project):
    """Fails closed on a value the state machine does not know."""
    ctx, db = await _session(project["org"])
    try:
        ref = await snapshot_stage_payload(
            db, tenant_id=project["org"], project_id=project["project"],
            stage="requirements", payload={"stories": [1]}, produced_by=PRODUCER)
        with pytest.raises((DBAPIError, IntegrityError), match="ck_artifact_versions_status"):
            await db.execute(text(
                "UPDATE artifact_versions SET status='approved' "
                "WHERE id = CAST(:i AS uuid)"
            ), {"i": ref.id})
            await db.flush()
    finally:
        await _close(ctx, db)


# -- reads --------------------------------------------------------------------


async def test_nothing_published_reads_as_none_not_as_the_draft(project):
    """THE FALLBACK THAT MUST NEVER EXIST. "No approved design yet" is the answer;
    returning the draft would make the whole gate decorative, exactly as the
    tenant-wide credential fallback made "Needs a credential" decorative."""
    ctx, db = await _session(project["org"])
    try:
        await snapshot_stage_payload(
            db, tenant_id=project["org"], project_id=project["project"],
            stage="design", payload={"c4": "x"}, produced_by=PRODUCER)
        assert await latest_version(db, project["project"], "design") is not None
        assert await latest_published(db, project["project"], "design") is None
    finally:
        await _close(ctx, db)


async def test_latest_published_ignores_a_newer_draft(project):
    """A draft v2 must not shadow the published v1 a consumer is entitled to read."""
    ctx, db = await _session(project["org"])
    try:
        kw = dict(tenant_id=project["org"], project_id=project["project"],
                  stage="design", produced_by=PRODUCER)
        v1 = await snapshot_stage_payload(db, payload={"c4": "one"}, **kw)
        await db.execute(text(
            "UPDATE artifact_versions SET status='published', published_by=:o, "
            "published_at=now() WHERE id = CAST(:i AS uuid)"
        ), {"o": OWNER, "i": v1.id})
        v2 = await snapshot_stage_payload(db, payload={"c4": "two"}, **kw)
        await db.flush()

        assert (await latest_version(db, project["project"], "design")).version == v2.version
        published = await latest_published(db, project["project"], "design")
        assert published.version == 1 and published.payload == {"c4": "one"}
    finally:
        await _close(ctx, db)


async def test_list_versions_can_be_scoped_to_one_stage(project):
    ctx, db = await _session(project["org"])
    try:
        kw = dict(tenant_id=project["org"], project_id=project["project"],
                  produced_by=PRODUCER)
        await snapshot_stage_payload(db, stage="requirements", payload={"a": 1}, **kw)
        await snapshot_stage_payload(db, stage="design", payload={"b": 1}, **kw)
        await db.flush()
        assert len(await list_versions(db, project["project"])) == 2
        assert len(await list_versions(db, project["project"], "design")) == 1
    finally:
        await _close(ctx, db)


# -- tenant isolation ---------------------------------------------------------


async def test_another_tenant_cannot_see_these_versions(project):
    """FORCE RLS on app.current_tenant_id. The app role is not a Postgres superuser,
    so this is real isolation rather than a convention."""
    ctx, db = await _session(project["org"])
    try:
        await snapshot_stage_payload(
            db, tenant_id=project["org"], project_id=project["project"],
            stage="requirements", payload={"secret": "theirs"}, produced_by=PRODUCER)
        await db.flush()
        assert len(await list_versions(db, project["project"])) == 1

        # Same rows, a different tenant's GUC.
        await db.execute(text("SELECT set_config('app.current_tenant_id', :t, true)"),
                         {"t": str(_uuid.uuid4())})
        assert await list_versions(db, project["project"]) == []
        assert await latest_version(db, project["project"], "requirements") is None
    finally:
        await _close(ctx, db)
