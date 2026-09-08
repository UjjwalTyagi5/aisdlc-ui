"""The publication gate — phase 2.

Phase 1 made a version that cannot change. This is the decision taken ON one: a single
signature from the role that owns the stage, after which every consumer may read it.

WHAT THESE PROTECT, each a way an approval could mean less than it appears to:

  self-publication   the producer signing their own work
  a reversed decision a rejected version quietly published later
  going backwards    an older version superseding newer approved work
  a lost predecessor the version a previous run consumed being destroyed
  an empty audit     a record that cannot say WHAT was signed

Against the real database, like `test_artifact_versions.py` and for the same reason:
half of this is enforced by CHECK constraints, and a fake session cannot execute one.
"""
from __future__ import annotations

import sys
import uuid as _uuid
from pathlib import Path

import pytest
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.db import get_db_session_superuser  # noqa: E402
from shared.services.artifact_versions import (  # noqa: E402
    PublicationRefused, latest_published, list_versions, publish_version,
    reject_version, snapshot_stage_payload,
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
            "VALUES (:i, :s, 'Artifact Publication Test')"
        ), {"i": org, "s": f"pub-{org[:8]}"})
        await s.execute(text(
            "INSERT INTO workspaces (id, organization_id, slug, display_name) "
            "VALUES (:i, :o, 'unit', 'Unit')"
        ), {"i": bu, "o": org})
        await s.execute(
            text("SELECT set_config('app.current_tenant_id', :t, true)"), {"t": org})
        await s.execute(text(
            "INSERT INTO projects (id, workspace_id, tenant_id, display_name) "
            "VALUES (:i, :w, :t, 'Pub Project')"
        ), {"i": proj, "w": bu, "t": org})
        await s.commit()
    yield {"org": org, "bu": bu, "project": proj}


async def _session(tenant_id: str):
    ctx = get_db_session_superuser()
    db = await ctx.__aenter__()
    await db.execute(
        text("SELECT set_config('app.current_tenant_id', :t, true)"), {"t": tenant_id})
    return ctx, db


async def _draft(db, project, stage="requirements", payload=None, producer=PRODUCER):
    return await snapshot_stage_payload(
        db, tenant_id=project["org"], project_id=project["project"], stage=stage,
        payload=payload if payload is not None else {"stories": [1]},
        produced_by=producer)


async def _publish(db, project, stage, version, by=OWNER):
    return await publish_version(
        db, tenant_id=project["org"], project_id=project["project"], stage=stage,
        version=version, published_by=by)


async def _reject(db, project, stage, version, by=OWNER, reason="not ready"):
    return await reject_version(
        db, tenant_id=project["org"], project_id=project["project"], stage=stage,
        version=version, rejected_by=by, reason=reason)


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


# -- publishing ---------------------------------------------------------------


async def test_publishing_marks_the_version_and_names_the_publisher(project):
    ctx, db = await _session(project["org"])
    try:
        ref = await _draft(db, project)
        row = await _publish(db, project, "requirements", ref.version)
        assert row.status == "published"
        assert row.published_by == OWNER and row.published_at is not None
        latest = await latest_published(db, project["project"], "requirements")
        assert latest.version == 1
    finally:
        await _close(ctx, db)


async def test_the_producer_cannot_publish_their_own_version(project):
    """THE RULE THE GATE RESTS ON. The agent runs AS A PERSON, so the comparison is
    always available — matching the run gate and the deployment gate, both verified
    live this session to hold even for the person who holds the permission."""
    ctx, db = await _session(project["org"])
    try:
        ref = await _draft(db, project)
        with pytest.raises(PublicationRefused) as exc:
            await _publish(db, project, "requirements", ref.version, by=PRODUCER)
        assert exc.value.code == "self_publication"
    finally:
        await _close(ctx, db)


async def test_publishing_supersedes_rather_than_deletes_the_previous(project):
    """A run that consumed v1 must keep being able to say what it built on."""
    ctx, db = await _session(project["org"])
    try:
        v1 = await _draft(db, project, payload={"s": 1})
        await _publish(db, project, "requirements", v1.version)
        v2 = await _draft(db, project, payload={"s": 2})
        await _publish(db, project, "requirements", v2.version)

        rows = {r.version: r.status
                for r in await list_versions(db, project["project"], "requirements")}
        assert rows == {1: "superseded", 2: "published"}
        latest = await latest_published(db, project["project"], "requirements")
        assert latest.version == 2
        # The superseded payload is still readable, not tombstoned.
        assert [r for r in await list_versions(db, project["project"], "requirements")
                if r.version == 1][0].payload == {"s": 1}
    finally:
        await _close(ctx, db)


async def test_publishing_an_older_version_is_refused(project):
    """It would supersede newer approved work with older content. Occasionally
    legitimate — that is a phase 4 request, never a silent default."""
    ctx, db = await _session(project["org"])
    try:
        v1 = await _draft(db, project, payload={"s": 1})
        v2 = await _draft(db, project, payload={"s": 2})
        await _publish(db, project, "requirements", v2.version)
        with pytest.raises(PublicationRefused) as exc:
            await _publish(db, project, "requirements", v1.version)
        assert exc.value.code == "would_go_backwards"
    finally:
        await _close(ctx, db)


async def test_republishing_is_idempotent(project):
    """A double-click must not raise, and must not re-supersede anything."""
    ctx, db = await _session(project["org"])
    try:
        ref = await _draft(db, project)
        first = await _publish(db, project, "requirements", ref.version)
        again = await _publish(db, project, "requirements", ref.version)
        assert again.id == first.id and again.status == "published"
    finally:
        await _close(ctx, db)


async def test_a_missing_version_is_not_found_rather_than_a_crash(project):
    ctx, db = await _session(project["org"])
    try:
        with pytest.raises(PublicationRefused) as exc:
            await _publish(db, project, "requirements", 99)
        assert exc.value.code == "not_found"
    finally:
        await _close(ctx, db)


async def test_publishing_one_stage_does_not_publish_another(project):
    """Decisions are per stage. A shared one would let signing off Requirements
    silently accept the Design nobody read."""
    ctx, db = await _session(project["org"])
    try:
        await _draft(db, project, stage="requirements", payload={"a": 1})
        await _draft(db, project, stage="design", payload={"b": 1})
        await _publish(db, project, "requirements", 1)
        assert await latest_published(db, project["project"], "design") is None
    finally:
        await _close(ctx, db)


# -- rejecting ----------------------------------------------------------------


async def test_a_rejected_version_cannot_then_be_published(project):
    """Publishing it would quietly overturn a decision somebody took."""
    ctx, db = await _session(project["org"])
    try:
        ref = await _draft(db, project)
        await _reject(db, project, "requirements", ref.version,
                      reason="missing acceptance criteria")
        with pytest.raises(PublicationRefused) as exc:
            await _publish(db, project, "requirements", ref.version)
        assert exc.value.code == "already_decided"
    finally:
        await _close(ctx, db)


async def test_a_rejection_must_carry_a_reason(project):
    """An unexplained rejection is indistinguishable from a mistake to whoever has to
    act on it. Whitespace is not a reason."""
    ctx, db = await _session(project["org"])
    try:
        ref = await _draft(db, project)
        with pytest.raises(PublicationRefused) as exc:
            await _reject(db, project, "requirements", ref.version, reason="   ")
        assert exc.value.code == "no_reason"
    finally:
        await _close(ctx, db)


async def test_the_producer_cannot_reject_their_own_version_either(project):
    """Less dangerous than self-publication, but still a decision recorded as
    independent review when it was not."""
    ctx, db = await _session(project["org"])
    try:
        ref = await _draft(db, project)
        with pytest.raises(PublicationRefused) as exc:
            await _reject(db, project, "requirements", ref.version, by=PRODUCER)
        assert exc.value.code == "self_publication"
    finally:
        await _close(ctx, db)


async def test_a_rejection_keeps_the_version_readable(project):
    """A rejection is a decision, not a deletion — the next run needs to see what was
    turned down and why."""
    ctx, db = await _session(project["org"])
    try:
        ref = await _draft(db, project, payload={"draft": True})
        row = await _reject(db, project, "requirements", ref.version, reason="thin")
        assert row.status == "rejected" and row.rejection_reason == "thin"
        assert row.payload == {"draft": True}
    finally:
        await _close(ctx, db)


# -- the audit trail ----------------------------------------------------------


async def test_the_audit_row_carries_the_hash_and_the_payload(project):
    """What makes the entry EVIDENCE rather than a note. The payload is copied in so
    the record of what was signed does not depend on the artifact_versions row
    surviving unedited — the freeze trigger makes that unlikely, but an audit trail
    that trusts the thing it audits is the wrong shape."""
    ctx, db = await _session(project["org"])
    try:
        ref = await _draft(db, project, payload={"stories": ["only this"]})
        await _publish(db, project, "requirements", ref.version)
        await db.flush()
        entry = (await db.execute(text(
            "SELECT payload FROM audit_events "
            "WHERE event_type = 'artifact_version_publish' AND resource_id = :r"
        ), {"r": ref.id})).scalar_one()
        assert entry["content_hash"] == ref.content_hash
        assert entry["payload"] == {"stories": ["only this"]}
        assert entry["stage"] == "requirements" and entry["version"] == 1
        assert entry["produced_by"] == PRODUCER
    finally:
        await _close(ctx, db)


async def test_a_rejection_is_audited_with_its_reason(project):
    ctx, db = await _session(project["org"])
    try:
        ref = await _draft(db, project)
        await _reject(db, project, "requirements", ref.version, reason="no criteria")
        await db.flush()
        entry = (await db.execute(text(
            "SELECT payload FROM audit_events "
            "WHERE event_type = 'artifact_version_reject' AND resource_id = :r"
        ), {"r": ref.id})).scalar_one()
        assert entry["reason"] == "no criteria"
        assert entry["content_hash"] == ref.content_hash
    finally:
        await _close(ctx, db)


async def test_superseding_records_what_it_replaced(project):
    """"Which version did this replace" has to be answerable from the audit alone."""
    ctx, db = await _session(project["org"])
    try:
        v1 = await _draft(db, project, payload={"s": 1})
        await _publish(db, project, "requirements", v1.version)
        v2 = await _draft(db, project, payload={"s": 2})
        await _publish(db, project, "requirements", v2.version)
        await db.flush()
        entry = (await db.execute(text(
            "SELECT payload FROM audit_events "
            "WHERE event_type = 'artifact_version_publish' AND resource_id = :r"
        ), {"r": v2.id})).scalar_one()
        assert entry["supersedes"] == 1
    finally:
        await _close(ctx, db)
