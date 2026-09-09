"""Pulling from a board project with no work items empties the story list.

WHAT WENT WRONG, and nothing errored. The Requirements page materialises its stories from
`Run.requirements_payload["stories"]`, taking the most recently updated requirements run
that yields any. The loop skipped a run whose synthesis came back empty:

    for r in req_runs:                      # newest first
        synth = story_artifacts_from_run(r, ...)
        if synth:                           # an empty pull is falsy
            result.extend(synth); break     # so it fell through to an OLDER run

So ingesting a board project with no work items fell through to the previous ingest and
re-displayed its stories. The user pulled from an empty ADO project, was told "No work
items found", and watched the list keep showing fifteen stories from somewhere else. The
page did not fail — it refused to change, which is harder to notice and easier to
disbelieve.

THE SKIP EXISTS FOR A REAL CASE and is kept: a chat run never writes
`requirements_payload` at all, and must not shadow the ingest that did. That is a MISSING
key. A key holding an empty list is a different statement — an ingest that looked and
found nothing — and it is the newest answer to "what is on this board".

Both halves are asserted below, because a fix that simply always took the newest run
would break the chat-run case and no existing test covers it.
"""
from __future__ import annotations

import sys
import uuid as _uuid
from datetime import datetime, timedelta, timezone
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
async def _dispose_shared_engine():
    yield
    from shared.db import engine
    await engine.dispose()


@pytest.fixture
async def project():
    org, bu, proj = str(_uuid.uuid4()), str(_uuid.uuid4()), str(_uuid.uuid4())
    async with get_db_session_superuser() as s:
        await s.execute(text(
            "INSERT INTO organizations (id, slug, display_name) VALUES (:i, :s, 'Pull')"
        ), {"i": org, "s": f"pull-{org[:8]}"})
        await s.execute(text(
            "INSERT INTO workspaces (id, organization_id, slug, display_name) "
            "VALUES (:i, :o, 'unit', 'Unit')"
        ), {"i": bu, "o": org})
    async with get_db_session_for_tenant(org) as s:
        await s.execute(text(
            "INSERT INTO projects (id, workspace_id, tenant_id, display_name) "
            "VALUES (:i, :w, :t, 'Pulled')"
        ), {"i": proj, "w": bu, "t": org})

    yield {"org": org, "bu": bu, "proj": proj}

    async with get_db_session_for_tenant(org) as s:
        await s.execute(text("DELETE FROM role_bindings"))
        await s.execute(text("DELETE FROM runs"))
        await s.execute(text("DELETE FROM projects"))
    async with get_db_session_superuser() as s:
        await s.execute(text(
            "DELETE FROM workspaces WHERE organization_id = CAST(:t AS uuid)"), {"t": org})
        await s.execute(text(
            "DELETE FROM organizations WHERE id = CAST(:t AS uuid)"), {"t": org})


async def _run(project, *, payload, minutes_ago: int) -> str:
    """A requirements run with an explicit `updated_at`, since that is the ordering key.

    `payload=None` writes SQL NULL — the chat-run case, a run that never recorded any
    requirements at all.
    """
    import json

    run = str(_uuid.uuid4())
    when = datetime.now(tz=timezone.utc) - timedelta(minutes=minutes_ago)
    async with get_db_session_for_tenant(project["org"]) as s:
        await s.execute(text(
            "INSERT INTO runs (id, project_id, tenant_id, stage, status, trigger, "
            "  requirements_payload, updated_at) "
            "VALUES (CAST(:i AS uuid), CAST(:p AS uuid), CAST(:t AS uuid), "
            "  'requirements', 'completed', 'manual', "
            "  CAST(:pl AS jsonb), :u)"
        ), {"i": run, "p": project["proj"], "t": project["org"],
            "pl": json.dumps(payload) if payload is not None else None, "u": when})
    return run


def _stories(project, user, perms):
    r = TestClient(process_api.app).get(
        f"/projects/{project['proj']}/artifacts",
        headers={
            "Authorization": "Bearer " + create_access_token(
                user_id=user, tenant_id=project["org"], permissions=perms),
            "X-Workspace-Id": project["bu"],
        },
    )
    assert r.status_code == 200, r.text
    return [a for a in r.json() if a["type"] == "story"]


async def _member(project) -> str:
    user = f"dev-{_uuid.uuid4()}"
    await grant_role(user, project["proj"], "developer",
                     tenant_id=project["org"], scope_kind="project")
    return user


FIFTEEN = {"stories": [
    {"id": str(n), "source_key": f"WI-{n}", "title": f"Task {n}"} for n in range(15)
]}


@pytest.mark.asyncio
async def test_an_empty_pull_clears_the_previous_ones_stories(project):
    """THE HEADLINE, and exactly what was reported: pull an empty board project and the
    list still showed the fifteen stories from the pull before it."""
    await _run(project, payload=FIFTEEN, minutes_ago=10)
    await _run(project, payload={"stories": [], "board_project": "URL Shortener"},
               minutes_ago=1)
    user = await _member(project)

    assert _stories(project, user, ["artifact:view"]) == []


@pytest.mark.asyncio
async def test_a_non_empty_pull_still_wins(project):
    """NON-VACUITY. The newest ingest is the answer whether or not it is empty — a fix
    that only ever returned nothing would pass the test above."""
    await _run(project, payload={"stories": []}, minutes_ago=10)
    await _run(project, payload=FIFTEEN, minutes_ago=1)
    user = await _member(project)

    assert len(_stories(project, user, ["artifact:view"])) == 15


@pytest.mark.asyncio
async def test_a_run_that_never_recorded_requirements_does_not_shadow_the_ingest(project):
    """THE CASE THE ORIGINAL SKIP EXISTED FOR, and the one a naive "always take the
    newest run" fix would break.

    A chat run is created once per project and reused, so it is frequently the most
    recently updated requirements run while having no `requirements_payload` at all.
    Letting it win would blank the story list every time somebody opened the chat.
    """
    await _run(project, payload=FIFTEEN, minutes_ago=10)
    await _run(project, payload=None, minutes_ago=1)  # a chat run
    user = await _member(project)

    assert len(_stories(project, user, ["artifact:view"])) == 15


@pytest.mark.asyncio
async def test_a_payload_without_a_stories_key_is_also_skipped(project):
    """Same distinction, one step subtler: a run that recorded SOMETHING about
    requirements but never a story list has not answered this question either."""
    await _run(project, payload=FIFTEEN, minutes_ago=10)
    await _run(project, payload={"handoff": "notes only"}, minutes_ago=1)
    user = await _member(project)

    assert len(_stories(project, user, ["artifact:view"])) == 15
