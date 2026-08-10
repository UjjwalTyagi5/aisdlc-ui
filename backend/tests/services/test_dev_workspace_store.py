"""DB-backed tests for dev_workspace_store.

Requires live Postgres on :5433 with migrations at head (0029_dev_workspaces).
Uses deterministic UUIDs so cleanup is precise and never races with other suites.
Skip gracefully when no DB connection is available.

All async tests share the session-scoped event loop from conftest.py.
"""
from __future__ import annotations

import uuid

import pytest

try:
    from shared.db import RESOLVED_POSTGRES_CONN_STRING as _CONN
    _DB_AVAILABLE = bool(_CONN) and "placeholder" not in _CONN
except Exception:
    _DB_AVAILABLE = False

_TENANT = "00000000-0000-0000-0004-000000000001"
_PROJECT = "00000000-0000-0000-0004-000000000010"

_skip_no_db = pytest.mark.skipif(
    not _DB_AVAILABLE,
    reason="No DB connection available — skipping DB-dependent dev_workspace_store tests",
)

pytestmark = pytest.mark.asyncio(loop_scope="session")


@pytest.fixture(autouse=True)
async def _cleanup():
    """Delete test rows created by this module after each test."""
    from sqlalchemy import delete

    from shared.db import get_db_session_for_tenant
    from shared.models.orm import DevWorkspace

    yield

    async with get_db_session_for_tenant(_TENANT) as session:
        await session.execute(
            delete(DevWorkspace).where(
                DevWorkspace.tenant_id == uuid.UUID(_TENANT)
            )
        )


@_skip_no_db
async def test_upsert_then_get_round_trips():
    """upsert creates a row; get_for_project returns the same fields."""
    from shared.services.dev_workspace_store import get_for_project, upsert

    fields = {
        "ado_project": "MyProject",
        "repo_name": "my-repo",
        "branch": "main",
        "remote_url": "https://dev.azure.com/org/MyProject/_git/my-repo",
        "work_dir": "/tmp/ws/my-repo",
        "status": "pulling",
        "pulled_by": "user-abc",
    }
    result = await upsert(_TENANT, _PROJECT, fields)
    assert result["ado_project"] == "MyProject"
    assert result["repo_name"] == "my-repo"
    assert result["status"] == "pulling"

    fetched = await get_for_project(_TENANT, _PROJECT)
    assert fetched is not None
    assert fetched["project_id"] == uuid.UUID(_PROJECT)
    assert fetched["ado_project"] == "MyProject"
    assert fetched["branch"] == "main"


@_skip_no_db
async def test_second_upsert_replaces_first():
    """A second upsert for the same (tenant, project) replaces fields — one row."""
    from sqlalchemy import select

    from shared.db import get_db_session_for_tenant
    from shared.models.orm import DevWorkspace
    from shared.services.dev_workspace_store import get_for_project, upsert

    first_fields = {
        "ado_project": "MyProject",
        "repo_name": "my-repo",
        "branch": "main",
        "remote_url": "https://dev.azure.com/org/MyProject/_git/my-repo",
        "work_dir": "/tmp/ws/first",
        "status": "pulling",
    }
    await upsert(_TENANT, _PROJECT, first_fields)

    second_fields = {
        "ado_project": "MyProject",
        "repo_name": "my-repo",
        "branch": "feature/x",
        "remote_url": "https://dev.azure.com/org/MyProject/_git/my-repo",
        "work_dir": "/tmp/ws/second",
        "status": "ready",
        "commit_sha": "abc123",
    }
    await upsert(_TENANT, _PROJECT, second_fields)

    fetched = await get_for_project(_TENANT, _PROJECT)
    assert fetched is not None
    assert fetched["branch"] == "feature/x"
    assert fetched["status"] == "ready"
    assert fetched["commit_sha"] == "abc123"

    async with get_db_session_for_tenant(_TENANT) as session:
        rows = (
            await session.execute(
                select(DevWorkspace).where(
                    DevWorkspace.tenant_id == uuid.UUID(_TENANT),
                    DevWorkspace.project_id == uuid.UUID(_PROJECT),
                )
            )
        ).scalars().all()
    assert len(rows) == 1, "Unique constraint must hold — exactly one row per (tenant, project)"


@_skip_no_db
async def test_get_for_project_missing_returns_none():
    """get_for_project returns None when no row exists for the project."""
    from shared.services.dev_workspace_store import get_for_project

    missing_project = "00000000-0000-0000-0004-000000000099"
    result = await get_for_project(_TENANT, missing_project)
    assert result is None
