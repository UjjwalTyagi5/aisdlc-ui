"""Proves upstream Requirements/Design context resolves by PROJECT, not by session id.

Before this task: opening the standalone Development page fresh mints a brand-new
random session id (createConversation -> a fresh uuid4() session_id server-side),
unrelated to whatever session Requirements/Design used for theirs. A session-keyed
lookup (fetch_session_artifacts(session_id)) on that fresh id finds nothing even on
a project where Requirements and Design have both been baselined. See
docs/superpowers/specs/2026-08-31-development-agent-verification-design.md Part 4.3."""
import uuid as _uuid

import pytest

from shared.db import get_db_session_for_tenant, get_db_session_superuser
from sqlalchemy import text

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def project_with_baselined_upstream_run():
    org = str(_uuid.uuid4())
    unit = str(_uuid.uuid4())
    project = str(_uuid.uuid4())
    async with get_db_session_superuser() as s:
        await s.execute(text(
            "INSERT INTO organizations (id, slug, display_name) VALUES (:i, :s, 'Upstream Ctx Test')"
        ), {"i": org, "s": f"ctx-{org[:8]}"})
        await s.execute(text(
            "INSERT INTO workspaces (id, organization_id, slug, display_name) "
            "VALUES (:i, :o, 'unit', 'Unit')"
        ), {"i": unit, "o": org})
    async with get_db_session_for_tenant(org) as s:
        await s.execute(text(
            "INSERT INTO projects (id, workspace_id, tenant_id, display_name) "
            "VALUES (:i, :w, :t, 'Upstream Ctx Project')"
        ), {"i": project, "w": unit, "t": org})
        await s.execute(text(
            "INSERT INTO runs (id, project_id, tenant_id, stage, status, requirements_payload, design_artifacts) "
            "VALUES (:i, :p, :t, 'design', 'completed', :req, :design)"
        ), {
            "i": str(_uuid.uuid4()), "p": project, "t": org,
            "req": '{"project": "TestBoard", "stories": [{"title": "As a user, I can log in"}]}',
            "design": '{"hld": "A three-tier web app.", "tech_stack": "FastAPI + Next.js"}',
        })
    yield {"org": org, "project": project}


async def test_build_context_for_project_finds_a_real_projects_baselined_requirements_and_design(
    project_with_baselined_upstream_run,
):
    from config.context_broker import build_context_for_project

    t = project_with_baselined_upstream_run
    ctx = await build_context_for_project(t["project"], t["org"], "development")

    assert "Requirements Context" in ctx or "REQUIREMENTS CONTEXT" in ctx
    assert "As a user, I can log in" in ctx
    assert "Design Context" in ctx or "DESIGN CONTEXT" in ctx
    assert "FastAPI + Next.js" in ctx


async def test_build_context_for_project_returns_empty_string_for_a_project_with_no_runs():
    org = str(_uuid.uuid4())
    unit = str(_uuid.uuid4())
    project = str(_uuid.uuid4())
    async with get_db_session_superuser() as s:
        await s.execute(text(
            "INSERT INTO organizations (id, slug, display_name) VALUES (:i, :s, 'Empty Ctx Test')"
        ), {"i": org, "s": f"empty-{org[:8]}"})
        await s.execute(text(
            "INSERT INTO workspaces (id, organization_id, slug, display_name) "
            "VALUES (:i, :o, 'unit', 'Unit')"
        ), {"i": unit, "o": org})
    async with get_db_session_for_tenant(org) as s:
        await s.execute(text(
            "INSERT INTO projects (id, workspace_id, tenant_id, display_name) "
            "VALUES (:i, :w, :t, 'Empty Ctx Project')"
        ), {"i": project, "w": unit, "t": org})

    from config.context_broker import build_context_for_project

    ctx = await build_context_for_project(project, org, "development")
    assert ctx == ""
