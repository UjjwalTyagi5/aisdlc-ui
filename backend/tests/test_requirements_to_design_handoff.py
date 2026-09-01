"""The Requirements → Design hand-off, exercised against the real database.

WHY THIS IS NOT OBVIOUS FROM READING. The payload changes stores on the way across, and
the two halves do not mention each other:

    write_requirements_artifact          Requirements agent tool
      -> artifact_service.write_and_notify
      -> persist_artifact                 writes runs.requirements_payload   ← store A
                                              |
    pipeline_session(input, "design")         |  the mirror, and the only bridge
      -> _read_run_upstream(run_id)           |  (_MIRROR_FIELDS)
      -> upsert_agent_session(...)        writes agent_sessions              ← store B
                                              |
    build_context(session_id, "design")       |
      -> fetch_session_artifacts          reads agent_sessions               ← store B
      -> _fmt_requirements                the block the Design model sees

`persist_artifact` writes only store A. `build_context` reads only store B. Design sees
nothing unless `pipeline_session` has run in between — so the hand-off depends on a
mirror that neither end references, and a change to `_MIRROR_FIELDS` would break it
silently. That is what these tests hold down.

REAL POSTGRES, not fakes. The mirror is the thing under test and it is made of two DB
round-trips; faking them would test the fake. Rows are created and removed per test.
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

pytestmark = pytest.mark.integration

STORY_TITLE = "Card holder can dispute a settled transaction"
BRD_MARKER = "Disputes must be raised within 120 days of settlement."


@pytest.fixture
async def seeded_run():
    """A tenant + project + run, torn down afterwards.

    The run is what both stores key on, so it has to be a real row: `persist_artifact`
    silently no-ops on a run it cannot find ("persist_artifact: run_id %s not found"),
    which would make every assertion below pass for the wrong reason.
    """
    from sqlalchemy import text

    from shared.db import get_db_session_for_tenant, get_db_session_superuser

    # The tenant table is `organizations`; `tenant_id` elsewhere is its id. Projects and
    # runs are FORCE RLS, so they are inserted through a tenant-scoped session (the GUC)
    # while the org and unit rows above them go in as superuser — the same split
    # test_documentation_agent_live_e2e.py uses.
    tenant_id = str(uuid.uuid4())
    workspace_id = str(uuid.uuid4())
    project_id = str(uuid.uuid4())
    run_id = str(uuid.uuid4())

    async with get_db_session_superuser() as s:
        await s.execute(
            text("INSERT INTO organizations (id, slug, display_name) "
                 "VALUES (:i, :sl, 'Handoff Test Co')"),
            {"i": tenant_id, "sl": f"handoff-{tenant_id[:8]}"},
        )
        await s.execute(
            text("INSERT INTO workspaces (id, organization_id, slug, display_name) "
                 "VALUES (:i, :o, 'payments', 'Payments')"),
            {"i": workspace_id, "o": tenant_id},
        )
    async with get_db_session_for_tenant(tenant_id) as s:
        await s.execute(
            text("INSERT INTO projects (id, workspace_id, tenant_id, display_name, "
                 "provider_kind, track) "
                 "VALUES (:i, :w, :t, 'Disputes', 'azure_devops', 'greenfield')"),
            {"i": project_id, "w": workspace_id, "t": tenant_id},
        )
        await s.execute(
            text("INSERT INTO runs (id, project_id, tenant_id, stage, status, "
                 "current_stage, gate_pending, trigger, created_at, updated_at) "
                 "VALUES (:i, :p, :t, 'requirements', 'running', 'requirements', "
                 "false, 'manual', now(), now())"),
            {"i": run_id, "p": project_id, "t": tenant_id},
        )

    yield {"run_id": run_id, "tenant_id": tenant_id, "project_id": project_id}

    async with get_db_session_superuser() as s:
        for stmt, param in (
            ("DELETE FROM agent_sessions WHERE session_id = :v", run_id),
            ("DELETE FROM runs WHERE id = CAST(:v AS uuid)", run_id),
            ("DELETE FROM projects WHERE id = CAST(:v AS uuid)", project_id),
            ("DELETE FROM workspaces WHERE id = CAST(:v AS uuid)", workspace_id),
            ("DELETE FROM organizations WHERE id = CAST(:v AS uuid)", tenant_id),
        ):
            try:
                await s.execute(text(stmt), {"v": param})
            except Exception:  # noqa: BLE001 — teardown must not mask a test failure
                pass


def _payload() -> dict:
    """The shape `write_requirements_artifact` actually produces (RequirementsArtifact
    .model_dump()) — not a hand-rolled dict that happens to satisfy the formatter."""
    from shared.models.artifacts import RequirementsArtifact

    return RequirementsArtifact(
        agent_session_id="unused-here",
        brd_content=BRD_MARKER,
        user_stories=[{"title": STORY_TITLE,
                       "acceptance_criteria": "Refund appears within 5 days"}],
        acceptance_criteria=["Dispute window is enforced at 120 days"],
        risk_register=[{"risk": "Scheme deadline changes", "severity": "medium"}],
    ).model_dump()


class _Input:
    """The minimum `pipeline_session` reads off its input."""

    def __init__(self, run_id, tenant_id):
        self.run_id = run_id
        self.tenant_id = tenant_id


# ── the hand-off ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_design_receives_what_requirements_wrote(seeded_run):
    """The whole chain, in the order it happens in a real run."""
    from config.context_broker import build_context
    from shared.services.artifact_service import persist_artifact
    from workflows.activities.pipeline_session import pipeline_session

    run_id, tenant_id = seeded_run["run_id"], seeded_run["tenant_id"]

    # 1. Requirements finishes and persists. (write_and_notify = this + a Redis
    #    publish; the publish is a notification, not part of the hand-off.)
    await persist_artifact(run_id, "requirements", _payload(), tenant_id=tenant_id)

    # 2. Design's turn begins — this is the step that mirrors A into B.
    async with pipeline_session(_Input(run_id, tenant_id), "design"):
        # 3. What the Design model is actually given.
        block = await build_context(run_id, "design")

    assert STORY_TITLE in block, "the user story did not survive the hand-off"
    assert BRD_MARKER in block, "the BRD content did not survive the hand-off"
    assert "REQUIREMENTS CONTEXT" in block


@pytest.mark.asyncio
async def test_without_the_mirror_design_sees_nothing(seeded_run):
    """The control, and the reason the test above is not vacuous.

    `persist_artifact` alone puts the payload in `runs` and nowhere else. If this ever
    starts passing, the two stores have been unified and the mirror is dead code —
    which is worth knowing, because the test above would then prove nothing.
    """
    from config.context_broker import build_context
    from shared.services.artifact_service import persist_artifact

    run_id, tenant_id = seeded_run["run_id"], seeded_run["tenant_id"]
    await persist_artifact(run_id, "requirements", _payload(), tenant_id=tenant_id)

    assert await build_context(run_id, "design") == ""


@pytest.mark.asyncio
async def test_the_payload_really_landed_in_the_runs_column(seeded_run):
    """`persist_artifact` no-ops on a run it cannot find. Without this, a broken fixture
    would look exactly like a broken hand-off."""
    from sqlalchemy import text

    from shared.db import get_db_session_for_tenant
    from shared.services.artifact_service import persist_artifact

    run_id, tenant_id = seeded_run["run_id"], seeded_run["tenant_id"]
    await persist_artifact(run_id, "requirements", _payload(), tenant_id=tenant_id)

    async with get_db_session_for_tenant(tenant_id) as s:
        stored = (await s.execute(
            text("SELECT requirements_payload FROM runs WHERE id = CAST(:r AS uuid)"),
            {"r": run_id},
        )).scalar_one()
    assert stored is not None
    assert stored["brd_content"] == BRD_MARKER


@pytest.mark.asyncio
async def test_an_unscoped_read_of_runs_returns_nothing(seeded_run):
    """The bug this file caught, pinned as its own fact.

    `runs` is FORCE RLS. A session with no `app.tenant_id` GUC reads zero rows — not an
    error, just silence. `_read_run_upstream` used a superuser session on the reasoning
    that a run-keyed pipeline read is a system operation, and so returned {} for every
    run in existence: nothing was mirrored, `build_context` returned "", and the Design
    agent received no requirements at all. Nothing logged, because "no upstream yet" is
    a legitimate state for the first stage.

    If this test ever fails, the isolation model changed and the fix above can be
    revisited — but until then, any pipeline read of `runs` needs a tenant.
    """
    from sqlalchemy import text

    from shared.db import get_db_session_for_tenant, get_db_session_superuser

    run_id, tenant_id = seeded_run["run_id"], seeded_run["tenant_id"]

    async with get_db_session_for_tenant(tenant_id) as s:
        scoped = (await s.execute(
            text("SELECT count(*) FROM runs WHERE id = CAST(:r AS uuid)"), {"r": run_id},
        )).scalar()
    async with get_db_session_superuser() as s:
        unscoped = (await s.execute(
            text("SELECT count(*) FROM runs WHERE id = CAST(:r AS uuid)"), {"r": run_id},
        )).scalar()

    assert scoped == 1
    assert unscoped == 0, "an unscoped read now sees runs — re-examine _read_run_upstream"


@pytest.mark.asyncio
async def test_the_mirror_reads_the_run_when_given_a_tenant(seeded_run):
    """The fix itself, at the seam: same run, with and without a tenant in scope."""
    from shared.services.artifact_service import persist_artifact
    from workflows.activities.pipeline_session import _read_run_upstream

    run_id, tenant_id = seeded_run["run_id"], seeded_run["tenant_id"]
    await persist_artifact(run_id, "requirements", _payload(), tenant_id=tenant_id)

    with_tenant = await _read_run_upstream(run_id, tenant_id)
    without = await _read_run_upstream(run_id)

    assert with_tenant.get("requirements_payload") is not None
    assert without == {}


@pytest.mark.asyncio
async def test_requirements_gets_no_upstream_context_of_its_own(seeded_run):
    """Requirements is pipeline position 1 with `input_artifacts=[]`. Feeding it its own
    output would let a re-run cite itself as a source."""
    from config.context_broker import build_context
    from shared.services.artifact_service import persist_artifact
    from workflows.activities.pipeline_session import pipeline_session

    run_id, tenant_id = seeded_run["run_id"], seeded_run["tenant_id"]
    await persist_artifact(run_id, "requirements", _payload(), tenant_id=tenant_id)

    async with pipeline_session(_Input(run_id, tenant_id), "requirements"):
        assert await build_context(run_id, "requirements") == ""


@pytest.mark.asyncio
async def test_the_mirror_carries_the_field_design_declares_it_needs(seeded_run):
    """`AGENT_REGISTRY["design"].input_artifacts` and `_MIRROR_FIELDS` are edited in
    different files by different people. A field dropped from the mirror breaks the
    hand-off with no error anywhere — build_context just returns ""."""
    from config.agent_registry import AGENT_REGISTRY
    from workflows.activities.pipeline_session import _MIRROR_FIELDS

    for field in AGENT_REGISTRY["design"].input_artifacts:
        assert field in _MIRROR_FIELDS, (
            f"design declares {field!r} as an input but pipeline_session does not "
            f"mirror it, so Design will never see it"
        )
