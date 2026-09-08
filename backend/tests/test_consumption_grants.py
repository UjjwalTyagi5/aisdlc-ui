"""Getting past the gate, and being told when the ground moves — phases 4 and 5.

PHASE 4. Three situations make consumer-side approval a real question rather than a
rubber stamp: an unpublished draft, a superseded version somebody wants to pin, and
another team's work. Everything else needs no request at all — the owning role signed
the version once and that signature serves every consumer. Requiring one per consumer
per artifact is up to seventy-two pairs per project, rubber-stamped inside a week,
producing an audit trail that looks like scrutiny and records none.

A GRANT IS NARROW: one version, one consuming stage. "Design may read drafts" would be
a standing licence indistinguishable from turning enforcement off.

PHASE 5. A consumer that built on v1 is TOLD when v2 publishes, never switched. Silent
upgrade is how a design change reaches production unreviewed, and it is
indistinguishable from correct behaviour until something breaks.
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
    granted_version, publish_version, snapshot_stage_payload,
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
            "VALUES (:i, :s, 'Consumption Grant Test')"
        ), {"i": org, "s": f"grant-{org[:8]}"})
        await s.execute(text(
            "INSERT INTO workspaces (id, organization_id, slug, display_name) "
            "VALUES (:i, :o, 'unit', 'Unit')"
        ), {"i": bu, "o": org})
        await s.execute(
            text("SELECT set_config('app.current_tenant_id', :t, true)"), {"t": org})
        await s.execute(text(
            "INSERT INTO projects (id, workspace_id, tenant_id, display_name, "
            "  enforce_artifact_publication) "
            "VALUES (:i, :w, :t, 'Grant Project', true)"
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


async def _draft(project, stage: str, payload: dict) -> str:
    """A draft version. Returns its id."""
    async with get_db_session_superuser() as s:
        await s.execute(
            text("SELECT set_config('app.current_tenant_id', :t, true)"),
            {"t": project["org"]})
        ref = await snapshot_stage_payload(
            s, tenant_id=project["org"], project_id=project["project"], stage=stage,
            payload=payload, produced_by=PRODUCER)
        await s.commit()
    return ref.id


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


async def _grant(project, version_id: str, consumer_stage: str,
                 reason: str = "needed for the spike"):
    async with get_db_session_superuser() as s:
        await s.execute(
            text("SELECT set_config('app.current_tenant_id', :t, true)"),
            {"t": project["org"]})
        await s.execute(text(
            "INSERT INTO artifact_consumption_grants "
            "  (tenant_id, project_id, version_id, consumer_stage, granted_by, reason) "
            "VALUES (CAST(:t AS uuid), CAST(:p AS uuid), CAST(:v AS uuid), :c, :g, :r)"
        ), {"t": project["org"], "p": project["project"], "v": version_id,
            "c": consumer_stage, "g": OWNER, "r": reason})
        await s.commit()


async def _revoke(project, version_id: str, consumer_stage: str):
    async with get_db_session_superuser() as s:
        await s.execute(
            text("SELECT set_config('app.current_tenant_id', :t, true)"),
            {"t": project["org"]})
        await s.execute(text(
            "UPDATE artifact_consumption_grants SET revoked_at = now(), revoked_by = :b "
            "WHERE version_id = CAST(:v AS uuid) AND consumer_stage = :c"
        ), {"b": OWNER, "v": version_id, "c": consumer_stage})
        await s.commit()


async def _never():
    raise AssertionError("the legacy reader must not run under enforcement")


# -- phase 4: the grant lets exactly one thing through -------------------------


async def test_without_a_grant_a_draft_stays_refused(project):
    """The baseline. Every assertion below is only meaningful against this."""
    await _draft(project, "design", {"c4": "unapproved"})
    result = await read_upstream_for_agent(
        tenant_id=project["org"], project_id=project["project"], stage="design",
        consumer_stage="security", legacy_reader=lambda: _never())
    assert not result.found


async def test_a_grant_lets_the_named_consumer_read_the_draft(project):
    vid = await _draft(project, "design", {"c4": "unapproved"})
    await _grant(project, vid, "security")

    result = await read_upstream_for_agent(
        tenant_id=project["org"], project_id=project["project"], stage="design",
        consumer_stage="security", legacy_reader=lambda: _never())
    assert result.payload == {"c4": "unapproved"}


async def test_the_read_is_marked_as_an_exception(project):
    """`via_grant` is what stops an exception being reported as routine. An exception
    recorded as routine is how the next reviewer learns the gate means nothing."""
    vid = await _draft(project, "design", {"c4": "unapproved"})
    await _grant(project, vid, "security")

    result = await read_upstream_for_agent(
        tenant_id=project["org"], project_id=project["project"], stage="design",
        consumer_stage="security", legacy_reader=lambda: _never())
    assert result.via_grant is True

    # A published read must NOT be marked as one, or the flag says nothing.
    await _publish(project, "testing", {"suites": 1})
    published = await read_upstream_for_agent(
        tenant_id=project["org"], project_id=project["project"], stage="testing",
        consumer_stage="security", legacy_reader=lambda: _never())
    assert published.found and published.via_grant is False


async def test_a_grant_does_not_leak_to_another_consumer(project):
    """NARROW BY CONSTRUCTION. A grant to `security` must not open the draft to
    `code_review` — that would be a standing licence in all but name."""
    vid = await _draft(project, "design", {"c4": "unapproved"})
    await _grant(project, vid, "security")

    other = await read_upstream_for_agent(
        tenant_id=project["org"], project_id=project["project"], stage="design",
        consumer_stage="code_review", legacy_reader=lambda: _never())
    assert not other.found


async def test_a_grant_does_not_cover_the_next_draft(project):
    """The next version is a new question. A grant that covered it would let the
    producer publish anything past the gate by re-running."""
    v1 = await _draft(project, "design", {"c4": "one"})
    await _grant(project, v1, "security")
    await _draft(project, "design", {"c4": "two"})

    result = await read_upstream_for_agent(
        tenant_id=project["org"], project_id=project["project"], stage="design",
        consumer_stage="security", legacy_reader=lambda: _never())
    # Still the granted one, NOT the newer ungranted draft.
    assert result.payload == {"c4": "one"}


async def test_a_grant_does_not_cross_stages(project):
    """A grant on a design draft must not open a testing draft."""
    vid = await _draft(project, "design", {"c4": "unapproved"})
    await _grant(project, vid, "security")
    await _draft(project, "testing", {"suites": "unapproved"})

    result = await read_upstream_for_agent(
        tenant_id=project["org"], project_id=project["project"], stage="testing",
        consumer_stage="security", legacy_reader=lambda: _never())
    assert not result.found


async def test_a_revoked_grant_stops_letting_anything_through(project):
    vid = await _draft(project, "design", {"c4": "unapproved"})
    await _grant(project, vid, "security")
    await _revoke(project, vid, "security")

    result = await read_upstream_for_agent(
        tenant_id=project["org"], project_id=project["project"], stage="design",
        consumer_stage="security", legacy_reader=lambda: _never())
    assert not result.found


async def test_a_revoked_grant_row_survives(project):
    """Revoked, never deleted: a run that read under it has to stay explicable."""
    vid = await _draft(project, "design", {"c4": "unapproved"})
    await _grant(project, vid, "security")
    await _revoke(project, vid, "security")

    ctx, db = await _session(project["org"])
    try:
        row = (await db.execute(text(
            "SELECT reason, revoked_by FROM artifact_consumption_grants "
            "WHERE version_id = CAST(:v AS uuid)"
        ), {"v": vid})).fetchone()
        assert row is not None and row[0] == "needed for the spike"
        assert row[1] == OWNER
    finally:
        await _close(ctx, db)


async def test_a_published_version_still_wins_over_a_granted_draft(project):
    """The grant is a FALLBACK for when nothing is approved, not an override. If it
    took precedence, an old exception would quietly outrank new signed-off work."""
    vid = await _draft(project, "design", {"c4": "the draft"})
    await _grant(project, vid, "security")
    await _publish(project, "design", {"c4": "properly published"})

    result = await read_upstream_for_agent(
        tenant_id=project["org"], project_id=project["project"], stage="design",
        consumer_stage="security", legacy_reader=lambda: _never())
    assert result.payload == {"c4": "properly published"}
    assert result.via_grant is False


async def test_a_granted_read_is_still_recorded_as_a_consumption(project):
    """An exception is exactly the read an auditor most wants in the trail."""
    vid = await _draft(project, "design", {"c4": "unapproved"})
    await _grant(project, vid, "security")
    await read_upstream_for_agent(
        tenant_id=project["org"], project_id=project["project"], stage="design",
        consumer_stage="security", legacy_reader=lambda: _never())

    ctx, db = await _session(project["org"])
    try:
        n = (await db.execute(text(
            "SELECT count(*) FROM artifact_consumptions "
            "WHERE project_id = CAST(:p AS uuid)"
        ), {"p": project["project"]})).scalar()
        assert n == 1
    finally:
        await _close(ctx, db)


async def test_one_live_grant_per_version_and_consumer(project):
    """A second would not mean more access, only two rows to revoke and one missed."""
    from sqlalchemy.exc import IntegrityError

    vid = await _draft(project, "design", {"c4": "unapproved"})
    await _grant(project, vid, "security")
    with pytest.raises(IntegrityError):
        await _grant(project, vid, "security")


async def test_grants_are_tenant_isolated(project):
    vid = await _draft(project, "design", {"c4": "unapproved"})
    await _grant(project, vid, "security")

    ctx, db = await _session(str(_uuid.uuid4()))
    try:
        n = (await db.execute(
            text("SELECT count(*) FROM artifact_consumption_grants"))).scalar()
        assert n == 0
        assert await granted_version(
            db, project["project"], "design", "security") is None
    finally:
        await _close(ctx, db)


# -- phase 4: routing the request to the right human ---------------------------


def test_the_request_type_is_registered_and_system_raised():
    """Nobody picks this from a menu — an agent hitting the gate is what files it."""
    from shared.governance import routing

    assert "artifact_consumption" in routing.REQUEST_TYPES
    assert "artifact_consumption" in routing.SYSTEM_RAISED
    assert routing.REQUEST_TYPE_LABEL["artifact_consumption"] == "Artifact consumption"


def test_the_approver_is_the_owner_of_the_producing_stage():
    """NOT the requester's Project Admin. Tier routing would send a Developer's ask up
    a chain that never had standing to judge the design they want to read."""
    from shared.governance.routing import agent_owner_role

    assert agent_owner_role("design") == "architect"
    assert agent_owner_role("testing") == "qa"
    assert agent_owner_role("deployment") == "devops_engineer"


def test_the_approver_map_stays_exhaustive():
    """A new request type cannot be added without a routing decision."""
    from shared.governance.routing import GOVERNANCE_APPROVER_ROLE, REQUEST_TYPES

    assert set(GOVERNANCE_APPROVER_ROLE) == set(REQUEST_TYPES)


def test_an_effect_is_wired_for_the_type():
    """`apply_on_approve` raises EffectNotAvailable for a type nobody wired up, so an
    approval would fail at the moment somebody clicked it."""
    import inspect

    from shared.governance import effects

    src = inspect.getsource(effects)
    assert 'rtype == "artifact_consumption"' in src
    assert "_apply_artifact_consumption" in src


# -- phase 5: supersession notices ---------------------------------------------


async def test_a_consumer_is_notified_when_its_version_is_superseded(project):
    """TOLD, NOT SWITCHED."""
    await _publish(project, "design", {"c4": "v1"})
    await read_upstream_for_agent(
        tenant_id=project["org"], project_id=project["project"], stage="design",
        consumer_stage="security", legacy_reader=lambda: _never())
    await _publish(project, "design", {"c4": "v2"})

    ctx, db = await _session(project["org"])
    try:
        rows = (await db.execute(text(
            "SELECT title, recipient_role, recipient_scope_id FROM notifications "
            "WHERE kind = 'artifact_superseded' AND project_id = CAST(:p AS uuid)"
        ), {"p": project["project"]})).fetchall()
        assert len(rows) == 1, f"expected one notice, got {rows}"
        title, role, scope = rows[0]
        # Addressed to the CONSUMING stage's owner — the role that signs security off
        # is who has to judge whether the design change matters.
        assert role == "security_engineer"
        assert str(scope) == project["project"]
        assert "v2" in title and "v1" in title
    finally:
        await _close(ctx, db)


async def test_no_notice_when_nobody_consumed_the_old_version(project):
    """A bell that rings when nothing happened is a bell that stops being read."""
    await _publish(project, "design", {"c4": "v1"})
    await _publish(project, "design", {"c4": "v2"})

    ctx, db = await _session(project["org"])
    try:
        n = (await db.execute(text(
            "SELECT count(*) FROM notifications WHERE kind = 'artifact_superseded' "
            "AND project_id = CAST(:p AS uuid)"
        ), {"p": project["project"]})).scalar()
        assert n == 0
    finally:
        await _close(ctx, db)


async def test_one_notice_per_consuming_stage_not_per_read(project):
    """An agent that read its upstream four times has not become four audiences."""
    await _publish(project, "design", {"c4": "v1"})
    for _ in range(4):
        await read_upstream_for_agent(
            tenant_id=project["org"], project_id=project["project"], stage="design",
            consumer_stage="security", legacy_reader=lambda: _never())
    await _publish(project, "design", {"c4": "v2"})

    ctx, db = await _session(project["org"])
    try:
        n = (await db.execute(text(
            "SELECT count(*) FROM notifications WHERE kind = 'artifact_superseded' "
            "AND project_id = CAST(:p AS uuid)"
        ), {"p": project["project"]})).scalar()
        assert n == 1
    finally:
        await _close(ctx, db)


async def test_each_distinct_consumer_gets_its_own_notice(project):
    await _publish(project, "design", {"c4": "v1"})
    for consumer in ("security", "code_review"):
        await read_upstream_for_agent(
            tenant_id=project["org"], project_id=project["project"], stage="design",
            consumer_stage=consumer, legacy_reader=lambda: _never())
    await _publish(project, "design", {"c4": "v2"})

    ctx, db = await _session(project["org"])
    try:
        roles = (await db.execute(text(
            "SELECT recipient_role FROM notifications WHERE kind='artifact_superseded' "
            "AND project_id = CAST(:p AS uuid) ORDER BY recipient_role"
        ), {"p": project["project"]})).scalars().all()
        # security -> security_engineer, code_review -> architect
        assert sorted(roles) == ["architect", "security_engineer"]
    finally:
        await _close(ctx, db)


async def test_the_consumer_keeps_reading_the_old_version_until_it_runs_again(project):
    """NOT SWITCHED. The notice is the whole mechanism — a consumer pinned to v1 sees
    v2 only when it next reads, and the run that used v1 stays correct about that."""
    await _publish(project, "design", {"c4": "v1"})
    first = await read_upstream_for_agent(
        tenant_id=project["org"], project_id=project["project"], stage="design",
        consumer_stage="security", legacy_reader=lambda: _never())
    assert first.payload == {"c4": "v1"}

    await _publish(project, "design", {"c4": "v2"})

    ctx, db = await _session(project["org"])
    try:
        row = (await db.execute(text(
            "SELECT version FROM artifact_consumptions "
            "WHERE project_id = CAST(:p AS uuid)"
        ), {"p": project["project"]})).scalar()
        # The recorded consumption still names v1 — history is not rewritten.
        assert row == 1
    finally:
        await _close(ctx, db)

    # The NEXT read picks up v2, and records that it did.
    second = await read_upstream_for_agent(
        tenant_id=project["org"], project_id=project["project"], stage="design",
        consumer_stage="security", legacy_reader=lambda: _never())
    assert second.payload == {"c4": "v2"} and second.version == 2


async def test_a_failed_notice_does_not_roll_back_the_publication(project):
    """BEST EFFORT. A notification that cannot be written must never undo the thing it
    was announcing — the publication is the operation that matters."""
    from unittest.mock import patch

    from shared.services import notifications

    await _publish(project, "design", {"c4": "v1"})
    await read_upstream_for_agent(
        tenant_id=project["org"], project_id=project["project"], stage="design",
        consumer_stage="security", legacy_reader=lambda: _never())

    async def boom(*a, **k):
        raise RuntimeError("notification backend down")

    with patch.object(notifications, "emit", boom):
        await _publish(project, "design", {"c4": "v2"})

    ctx, db = await _session(project["org"])
    try:
        statuses = {v: s for v, s in (await db.execute(text(
            "SELECT version, status FROM artifact_versions "
            "WHERE project_id = CAST(:p AS uuid) AND stage = 'design'"
        ), {"p": project["project"]})).fetchall()}
        assert statuses == {1: "superseded", 2: "published"}
    finally:
        await _close(ctx, db)


# -- phase 5: the visibility matrix --------------------------------------------


async def test_the_matrix_covers_every_runnable_stage(project):
    """Derived from STAGE_ORDER, not a second hardcoded table. A matrix that could
    disagree with what the gate does would be worse than no matrix."""
    from shared.services.artifact_versions import consumption_matrix
    from shared.services.orchestrator.progression import STAGE_ORDER

    ctx, db = await _session(project["org"])
    try:
        rows = await consumption_matrix(db, project["project"])
        assert [r["stage"] for r in rows] == list(STAGE_ORDER)
    finally:
        await _close(ctx, db)


async def test_the_matrix_names_the_owner_who_can_actually_approve(project):
    """The whole point of the "who do I ask" column. Asserted against the live owner
    map so it cannot drift from the routing that files the request."""
    from shared.governance.routing import agent_owner_role
    from shared.services.artifact_versions import consumption_matrix

    ctx, db = await _session(project["org"])
    try:
        rows = {r["stage"]: r for r in await consumption_matrix(db, project["project"])}
        assert rows["design"]["ownerRole"] == agent_owner_role("design") == "architect"
        assert rows["plan"]["ownerRole"] == "scrum_master"
        assert rows["code_review"]["ownerRole"] == "architect"
    finally:
        await _close(ctx, db)


async def test_nothing_published_reads_as_needs_request(project):
    from shared.services.artifact_versions import consumption_matrix

    ctx, db = await _session(project["org"])
    try:
        rows = {r["stage"]: r for r in await consumption_matrix(db, project["project"])}
        assert rows["design"]["publishedVersion"] is None
        assert rows["design"]["consumers"]["security"] == "needs_request"
    finally:
        await _close(ctx, db)


async def test_a_published_stage_is_open_to_every_consumer(project):
    """PUBLISH ONCE, CONSUME FREELY. One signature serves every consumer — the matrix
    has to show that, or it implies per-consumer approval that does not exist."""
    from shared.services.artifact_versions import consumption_matrix

    await _publish(project, "design", {"c4": "signed"})
    ctx, db = await _session(project["org"])
    try:
        row = {r["stage"]: r for r in
               await consumption_matrix(db, project["project"])}["design"]
        assert row["publishedVersion"] == 1 and row["publishedBy"] == OWNER
        others = {c: v for c, v in row["consumers"].items() if c != "design"}
        assert set(others.values()) == {"open"}
    finally:
        await _close(ctx, db)


async def test_a_grant_shows_only_in_its_own_cell(project):
    """The exception must be visible AS an exception, and only where it applies."""
    from shared.services.artifact_versions import consumption_matrix

    vid = await _draft(project, "design", {"c4": "unapproved"})
    await _grant(project, vid, "security")

    ctx, db = await _session(project["org"])
    try:
        row = {r["stage"]: r for r in
               await consumption_matrix(db, project["project"])}["design"]
        assert row["consumers"]["security"] == "granted"
        assert row["consumers"]["code_review"] == "needs_request"
    finally:
        await _close(ctx, db)


async def test_a_revoked_grant_leaves_the_cell(project):
    from shared.services.artifact_versions import consumption_matrix

    vid = await _draft(project, "design", {"c4": "unapproved"})
    await _grant(project, vid, "security")
    await _revoke(project, vid, "security")

    ctx, db = await _session(project["org"])
    try:
        row = {r["stage"]: r for r in
               await consumption_matrix(db, project["project"])}["design"]
        assert row["consumers"]["security"] == "needs_request"
    finally:
        await _close(ctx, db)


async def test_a_stage_does_not_consume_itself(project):
    from shared.services.artifact_versions import consumption_matrix

    await _publish(project, "design", {"c4": "signed"})
    ctx, db = await _session(project["org"])
    try:
        row = {r["stage"]: r for r in
               await consumption_matrix(db, project["project"])}["design"]
        assert row["consumers"]["design"] == "self"
    finally:
        await _close(ctx, db)
