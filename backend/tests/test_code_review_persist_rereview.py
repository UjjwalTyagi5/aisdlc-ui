"""Re-reviewing a commit that was already reviewed must SAVE the new review.

The regression this pins: `_persist_review_to_run` used to skip its insert whenever any
run already carried the same `context.head_sha`. The reader clicked Run review, the
agent streamed a complete review into the chat, and nothing reached the Summary/Findings
tabs — no row, no error, no explanation. Reported live on 2026-09-03 against
feature/dup-banner-purple, whose commit had been reviewed the day before.

Idempotency (one artifact never persisted twice) is a separate property, held by
`last_artifact` being falsy-checked on entry and cleared after a successful save — it
never depended on the head_sha guard, so removing that guard cannot reintroduce
duplicates. Both properties are asserted here.
"""
import uuid as _uuid

import pytest
from sqlalchemy import text

from agents_orchestrator.code_review_agent.code_review_agent_api import _persist_review_to_run
from agents_orchestrator.code_review_agent.config.session_state import get_session
from shared.db import get_db_session_for_tenant, get_db_session_superuser

pytestmark = pytest.mark.usefixtures("purge_created_orgs")


@pytest.fixture(autouse=True)
async def _dispose_shared_engine():
    yield
    from shared.db import engine
    await engine.dispose()


HEAD = "ed17288d9965176516c5c3da05950f6a4152d7f0"


def _artifact() -> dict:
    return {
        "context": {"repo_name": "Company", "head_sha": HEAD, "base_sha": "e453093",
                    "mode": "branch", "source_branch": "feature/x", "base_branch": "main"},
        "summary": "a review",
        "merge_recommendation": "request_changes",
        "findings": [],
        "metrics": {"files_changed": 1, "added": 7, "removed": 7},
    }


@pytest.fixture
async def project():
    org = str(_uuid.uuid4())
    unit = str(_uuid.uuid4())
    proj = str(_uuid.uuid4())
    async with get_db_session_superuser() as s:
        await s.execute(text(
            "INSERT INTO organizations (id, slug, display_name) VALUES (:i, :s, 'RR Test')"
        ), {"i": org, "s": f"rr-{org[:8]}"})
        await s.execute(text(
            "INSERT INTO workspaces (id, organization_id, slug, display_name) "
            "VALUES (:i, :o, 'unit', 'Unit')"
        ), {"i": unit, "o": org})
    async with get_db_session_for_tenant(org) as s:
        await s.execute(text(
            "INSERT INTO projects (id, workspace_id, tenant_id, display_name) "
            "VALUES (:i, :w, :t, 'RR Project')"
        ), {"i": proj, "w": unit, "t": org})
    yield {"org": org, "project": proj}


async def _count_reviews(org: str, proj: str) -> int:
    async with get_db_session_for_tenant(org) as s:
        return (await s.execute(
            text("SELECT count(*) FROM runs WHERE project_id = CAST(:p AS uuid) "
                 "AND code_review_artifacts IS NOT NULL"),
            {"p": proj},
        )).scalar()


async def test_a_second_review_of_the_same_commit_is_still_saved(project):
    t = project
    session_id = f"rr-{_uuid.uuid4()}"

    for _ in range(2):
        get_session(session_id).last_artifact = _artifact()
        await _persist_review_to_run(session_id, t["project"], t["org"])

    assert await _count_reviews(t["org"], t["project"]) == 2, (
        "the re-review of an already-reviewed commit was discarded"
    )


async def test_one_artifact_is_never_persisted_twice(project):
    """Idempotency still holds: the second call has nothing to save because the first
    cleared `last_artifact`, so the two real call sites (WS and REST) cannot double up."""
    t = project
    session_id = f"rr-{_uuid.uuid4()}"

    get_session(session_id).last_artifact = _artifact()
    await _persist_review_to_run(session_id, t["project"], t["org"])
    await _persist_review_to_run(session_id, t["project"], t["org"])

    assert await _count_reviews(t["org"], t["project"]) == 1
