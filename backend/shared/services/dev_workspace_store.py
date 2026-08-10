"""Postgres store for per-project Development workspace state.

One row per (tenant_id, project_id). A re-pull replaces the existing row via
upsert — the unique constraint enforces the one-workspace-per-project invariant.
Reads return None on miss; writes raise on failure (workspace ops are user-facing,
not fire-and-forget like the agent hot path).
"""
from __future__ import annotations

import uuid
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from shared.db import get_db_session_for_tenant
from shared.models.orm import DevWorkspace

logger = logging.getLogger(__name__)

_WRITABLE_FIELDS = frozenset({
    "ado_project",
    "repo_name",
    "branch",
    "remote_url",
    "work_dir",
    "commit_sha",
    "status",
    "pulled_by",
    "updated_at",
})


def _to_dict(row: DevWorkspace) -> dict:
    return {
        "id": row.id,
        "tenant_id": row.tenant_id,
        "project_id": row.project_id,
        "ado_project": row.ado_project,
        "repo_name": row.repo_name,
        "branch": row.branch,
        "remote_url": row.remote_url,
        "work_dir": row.work_dir,
        "commit_sha": row.commit_sha,
        "status": row.status,
        "pulled_by": row.pulled_by,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


async def get_for_project(tenant_id: str, project_id: str) -> dict | None:
    async with get_db_session_for_tenant(tenant_id) as session:
        row = (
            await session.execute(
                select(DevWorkspace).where(
                    DevWorkspace.tenant_id == uuid.UUID(tenant_id),
                    DevWorkspace.project_id == uuid.UUID(project_id),
                )
            )
        ).scalar_one_or_none()
        return _to_dict(row) if row is not None else None


async def upsert(tenant_id: str, project_id: str, fields: dict[str, Any]) -> dict:
    updates = {k: v for k, v in fields.items() if k in _WRITABLE_FIELDS}
    tid = uuid.UUID(tenant_id)
    pid = uuid.UUID(project_id)

    from sqlalchemy import func as sa_func
    insert_values = {
        "tenant_id": tid,
        "project_id": pid,
        **updates,
    }
    on_conflict_set = {
        k: v for k, v in updates.items()
        if k != "updated_at"
    }
    on_conflict_set["updated_at"] = sa_func.now()

    async with get_db_session_for_tenant(tenant_id) as session:
        stmt = (
            pg_insert(DevWorkspace)
            .values(**insert_values)
            .on_conflict_do_update(
                index_elements=["tenant_id", "project_id"],
                set_=on_conflict_set,
            )
        )
        await session.execute(stmt)
        row = (
            await session.execute(
                select(DevWorkspace).where(
                    DevWorkspace.tenant_id == tid,
                    DevWorkspace.project_id == pid,
                )
            )
        ).scalar_one()
        return _to_dict(row)
