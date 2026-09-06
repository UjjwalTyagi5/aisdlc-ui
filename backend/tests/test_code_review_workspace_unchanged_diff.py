"""`_find_unchanged_review` — PRD §21.4: "Skips redundant re-review when nothing
changed since the last pass." A diff counts as unchanged only when the same repo's
head AND base sha both match a prior review; a same-named branch that moved, or a
different repo, must not match.
"""
import json
import uuid as _uuid

import pytest
from sqlalchemy import text

from shared.db import get_db_session_for_tenant, get_db_session_superuser
from shared.routers.code_review_workspace import _find_unchanged_review

pytestmark = pytest.mark.usefixtures("purge_created_orgs")


@pytest.fixture(autouse=True)
async def _dispose_shared_engine():
    yield
    from shared.db import engine
    await engine.dispose()


@pytest.fixture
async def project():
    org = str(_uuid.uuid4())
    unit = str(_uuid.uuid4())
    proj = str(_uuid.uuid4())
    async with get_db_session_superuser() as s:
        await s.execute(text(
            "INSERT INTO organizations (id, slug, display_name) VALUES (:i, :s, 'CRU Test')"
        ), {"i": org, "s": f"cru-{org[:8]}"})
        await s.execute(text(
            "INSERT INTO workspaces (id, organization_id, slug, display_name) "
            "VALUES (:i, :o, 'unit', 'Unit')"
        ), {"i": unit, "o": org})
    async with get_db_session_for_tenant(org) as s:
        await s.execute(text(
            "INSERT INTO projects (id, workspace_id, tenant_id, display_name) "
            "VALUES (:i, :w, :t, 'CRU Project')"
        ), {"i": proj, "w": unit, "t": org})
    yield {"org": org, "project": proj}


async def _seed_review(org: str, proj: str, *, repo: str, head: str, base: str) -> str:
    run_id = str(_uuid.uuid4())
    artifacts = {
        "context": {"repo_name": repo, "head_sha": head, "base_sha": base},
        "merge_recommendation": "approve",
        "findings": [],
    }
    async with get_db_session_for_tenant(org) as s:
        await s.execute(
            text(
                "INSERT INTO runs (id, project_id, tenant_id, code_review_artifacts) "
                "VALUES (:i, :p, :t, CAST(:a AS jsonb))"
            ),
            {"i": run_id, "p": proj, "t": org, "a": json.dumps(artifacts)},
        )
    return run_id


async def test_matches_a_prior_review_of_the_identical_diff(project):
    t = project
    run_id = await _seed_review(t["org"], t["project"], repo="r1", head="aaa", base="bbb")
    async with get_db_session_for_tenant(t["org"]) as s:
        found = await _find_unchanged_review(
            s, tenant_id=t["org"], project_id=t["project"],
            repo_name="r1", head_sha="aaa", base_sha="bbb",
        )
    assert found is not None
    assert str(found.id) == run_id


async def test_a_moved_head_sha_is_not_a_match(project):
    t = project
    await _seed_review(t["org"], t["project"], repo="r1", head="aaa", base="bbb")
    async with get_db_session_for_tenant(t["org"]) as s:
        found = await _find_unchanged_review(
            s, tenant_id=t["org"], project_id=t["project"],
            repo_name="r1", head_sha="ccc", base_sha="bbb",
        )
    assert found is None


async def test_a_different_repo_with_the_same_shas_is_not_a_match(project):
    t = project
    await _seed_review(t["org"], t["project"], repo="r1", head="aaa", base="bbb")
    async with get_db_session_for_tenant(t["org"]) as s:
        found = await _find_unchanged_review(
            s, tenant_id=t["org"], project_id=t["project"],
            repo_name="r2", head_sha="aaa", base_sha="bbb",
        )
    assert found is None


async def test_no_prior_reviews_at_all_is_not_a_match(project):
    t = project
    async with get_db_session_for_tenant(t["org"]) as s:
        found = await _find_unchanged_review(
            s, tenant_id=t["org"], project_id=t["project"],
            repo_name="r1", head_sha="aaa", base_sha="bbb",
        )
    assert found is None
