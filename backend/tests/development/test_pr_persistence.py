"""Integration test for _persist_pr_to_run — PR persistence to project-linked Run.

Tests:
  1. A PR session state writes exactly one Run with correct fields.
  2. Calling again with the same pr_url is idempotent (still exactly one Run).

Uses live Postgres at :5433 (db: sdlc_product) via get_db_session_for_tenant.
Seeds and tears down real Project/Organization/Workspace rows.

Both tests run in the same session-scoped event loop so the asyncpg pool is never
asked to operate across a closed loop boundary.
"""
from __future__ import annotations

import uuid

import pytest

# DB URL is KV-sourced at import time — use the resolved string, not the env var.
from shared.db import RESOLVED_POSTGRES_CONN_STRING as _DB_URL

_skip_no_db = pytest.mark.skipif(
    not _DB_URL or _DB_URL.endswith("sdlc_agentic"),  # placeholder URL = no real DB
    reason="Live Postgres not configured — skipping DB-dependent PR persistence tests",
)

# Deterministic IDs so teardown is always precise
_TENANT_ID  = uuid.UUID("00000000-b5b5-0000-0001-000000000001")
_ORG_ID     = uuid.UUID("00000000-b5b5-0000-0001-000000000010")
_WS_ID      = uuid.UUID("00000000-b5b5-0000-0001-000000000011")
_PROJ_ID    = uuid.UUID("00000000-b5b5-0000-0001-000000000012")

_PR_URL     = "https://dev.azure.com/o/p/_git/r/pullrequest/7"
_PR_TITLE   = "Add health"
_SESSION_ID = "test-b5-pr-persist-001"


@pytest.fixture(scope="module")
async def seeded_project():
    """Insert org/workspace/project; clean up on module teardown."""
    from sqlalchemy import text
    from shared.db import get_db_session_for_tenant

    async with get_db_session_for_tenant(str(_TENANT_ID)) as db:
        await db.execute(
            text("""
                INSERT INTO organizations (id, slug, display_name, created_at, updated_at)
                VALUES (:id, :slug, :dn, now(), now())
                ON CONFLICT (id) DO NOTHING
            """),
            {"id": str(_ORG_ID), "slug": "b5-test-org", "dn": "B5 Test Org"},
        )
        await db.execute(
            text("""
                INSERT INTO workspaces (id, organization_id, slug, display_name, created_at, updated_at)
                VALUES (:id, :org_id, :slug, :dn, now(), now())
                ON CONFLICT (id) DO NOTHING
            """),
            {"id": str(_WS_ID), "org_id": str(_ORG_ID), "slug": "b5-test-ws", "dn": "B5 Test WS"},
        )
        await db.execute(
            text("""
                INSERT INTO projects (id, workspace_id, tenant_id, display_name,
                                     provider_kind, archived, created_at, updated_at)
                VALUES (:id, :ws_id, :tid, :dn, 'azure_devops', false, now(), now())
                ON CONFLICT (id) DO NOTHING
            """),
            {
                "id": str(_PROJ_ID),
                "ws_id": str(_WS_ID),
                "tid": str(_TENANT_ID),
                "dn": "B5 Test Project",
            },
        )

    yield str(_PROJ_ID)

    # Teardown: remove Runs and Project rows seeded by this module
    async with get_db_session_for_tenant(str(_TENANT_ID)) as db:
        await db.execute(
            text("DELETE FROM runs WHERE project_id = :pid AND tenant_id = :tid"),
            {"pid": str(_PROJ_ID), "tid": str(_TENANT_ID)},
        )
        await db.execute(
            text("DELETE FROM projects WHERE id = :id"),
            {"id": str(_PROJ_ID)},
        )
        await db.execute(
            text("DELETE FROM workspaces WHERE id = :id"),
            {"id": str(_WS_ID)},
        )
        await db.execute(
            text("DELETE FROM organizations WHERE id = :id"),
            {"id": str(_ORG_ID)},
        )


@pytest.fixture(autouse=True)
async def _clean_runs_per_test(seeded_project):
    """Delete all test Runs before each test for a clean slate."""
    from sqlalchemy import text
    from shared.db import get_db_session_for_tenant

    async with get_db_session_for_tenant(str(_TENANT_ID)) as db:
        await db.execute(
            text("DELETE FROM runs WHERE project_id = :pid AND tenant_id = :tid"),
            {"pid": str(_PROJ_ID), "tid": str(_TENANT_ID)},
        )


@pytest.mark.asyncio(loop_scope="session")
@_skip_no_db
async def test_persist_pr_creates_one_run(seeded_project):
    """A PR in session state produces exactly one Run with correct fields."""
    from sqlalchemy import text

    from agents_orchestrator.development_agent.config.session_state import get_session, clear_session
    from agents_orchestrator.development_agent.development_agent_api import _persist_pr_to_run
    from shared.db import get_db_session_for_tenant

    # Ensure clean session state
    clear_session(_SESSION_ID)
    s = get_session(_SESSION_ID)
    s.pr_url = _PR_URL
    s.pr_title = _PR_TITLE
    s.dev_artifacts.branch_name = "dev/health"

    try:
        await _persist_pr_to_run(_SESSION_ID, seeded_project, str(_TENANT_ID))

        async with get_db_session_for_tenant(str(_TENANT_ID)) as db:
            rows = (
                await db.execute(
                    text("""
                        SELECT id, stage, status, development_artifacts
                        FROM runs
                        WHERE project_id = :pid
                          AND tenant_id  = :tid
                          AND development_artifacts->>'pr_url' = :pr_url
                    """),
                    {
                        "pid": str(_PROJ_ID),
                        "tid": str(_TENANT_ID),
                        "pr_url": _PR_URL,
                    },
                )
            ).fetchall()

        assert len(rows) == 1, f"Expected 1 Run, got {len(rows)}"
        row = rows[0]
        assert row.stage == "development"
        assert row.status == "completed"
        da = row.development_artifacts
        assert da["pr_url"] == _PR_URL
        assert da["pr_title"] == _PR_TITLE
        assert da["status"] == "pr_created"
    finally:
        clear_session(_SESSION_ID)


@pytest.mark.asyncio(loop_scope="session")
@_skip_no_db
async def test_persist_pr_idempotent(seeded_project):
    """Calling _persist_pr_to_run twice with the same pr_url keeps exactly one Run."""
    from sqlalchemy import text

    from agents_orchestrator.development_agent.config.session_state import get_session, clear_session
    from agents_orchestrator.development_agent.development_agent_api import _persist_pr_to_run
    from shared.db import get_db_session_for_tenant

    clear_session(_SESSION_ID)
    s = get_session(_SESSION_ID)
    s.pr_url = _PR_URL
    s.pr_title = _PR_TITLE
    s.dev_artifacts.branch_name = "dev/health"

    try:
        await _persist_pr_to_run(_SESSION_ID, seeded_project, str(_TENANT_ID))
        await _persist_pr_to_run(_SESSION_ID, seeded_project, str(_TENANT_ID))

        async with get_db_session_for_tenant(str(_TENANT_ID)) as db:
            count = (
                await db.execute(
                    text("""
                        SELECT COUNT(*) FROM runs
                        WHERE project_id = :pid
                          AND tenant_id  = :tid
                          AND development_artifacts->>'pr_url' = :pr_url
                    """),
                    {
                        "pid": str(_PROJ_ID),
                        "tid": str(_TENANT_ID),
                        "pr_url": _PR_URL,
                    },
                )
            ).scalar()

        assert count == 1, f"Expected idempotent — still 1 Run, got {count}"
    finally:
        clear_session(_SESSION_ID)
