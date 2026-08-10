"""Audit resource router.

Exposes read operations for the AuditEvent model. All routes are JWT-protected
(NOT in _EXEMPT_PATHS) and scope every query by request.state.tenant_id.

Routes:
  GET  /audit                          — paginated list (query: project_id, actor, action, page, page_size)
  GET  /runs/{run_id}/audit            — cursor-paginated, filterable, RBAC-gated run-scoped trail

Threat mitigations:
  - T-M4-01, T-M4-02: All queries filtered by tenant_id (no cross-tenant reads)
  - T-M8-13: run-scoped route requires resource_id == run_id AND tenant_id match
  - T-M8-14: require_permission("artifact:view", run_param="run_id") on run-scoped route
  - Route not in _EXEMPT_PATHS (JWT middleware enforces 401 without token)

Router mounting note (REQ-M8-06):
  audit_router       — mounted at /audit prefix in process_api.py
  audit_runs_router  — mounted WITHOUT a prefix in process_api.py
    so GET /runs/{run_id}/audit is the public path.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.authz.dependency import require_permission
from shared.db import get_db_session
from shared.models.orm import AuditEvent
from shared.routers._schemas import AuditEventOut, CursorPage, Paginated, Pagination

audit_router = APIRouter()

# Separate router for /runs/{run_id}/audit — mounted WITHOUT the /audit prefix
# so the public path resolves to /runs/{run_id}/audit (REQ-M8-06).
audit_runs_router = APIRouter()


@audit_router.get("", response_model=Paginated[AuditEventOut])
async def list_audit_events(
    request: Request,
    project_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
    actor: Optional[str] = None,
    action: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
    db: AsyncSession = Depends(get_db_session),
):
    """Return a paginated audit event list scoped to the requesting tenant.

    workspace_id filters to events whose payload.workspace_id matches — used by the
    workspace-scoped audit view and the org audit page's workspace picker.
    When absent, all tenant events are returned (org-level view).
    Falls back to the X-Workspace-Id header when workspace_id query param is absent,
    so the workspace audit tab scopes automatically without UI changes.
    """
    tenant_id = request.state.tenant_id

    # Resolve workspace scope: explicit query param → header → none (org-wide)
    effective_workspace = workspace_id
    if not effective_workspace:
        effective_workspace = request.headers.get("x-workspace-id") or None

    stmt = select(AuditEvent).where(AuditEvent.tenant_id == tenant_id)
    if effective_workspace:
        stmt = stmt.where(
            AuditEvent.payload["workspace_id"].astext == effective_workspace
        )
    if project_id:
        stmt = stmt.where(
            AuditEvent.payload["project_id"].astext == project_id
        )
    if actor:
        stmt = stmt.where(AuditEvent.actor_id == actor)
    if action:
        stmt = stmt.where(AuditEvent.event_type == action)

    count_stmt = select(func.count()).select_from(stmt.subquery())
    total: int = (await db.execute(count_stmt)).scalar_one()

    stmt = stmt.order_by(AuditEvent.created_at.desc())
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    rows = (await db.execute(stmt)).scalars().all()

    return Paginated(
        items=[AuditEventOut.from_orm_audit(e) for e in rows],
        pagination=Pagination(page=page, pageSize=page_size, total=total),
    )


@audit_runs_router.get(
    "/runs/{run_id}/audit",
    response_model=CursorPage[AuditEventOut],
    dependencies=[Depends(require_permission("artifact:view", run_param="run_id"))],
)
async def get_run_audit(
    run_id: str,
    request: Request,
    agent: Optional[str] = None,
    actor: Optional[str] = None,
    event_type: Optional[str] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
    cursor: Optional[str] = None,
    page_size: int = Query(50, le=200),
    db: AsyncSession = Depends(get_db_session),
) -> CursorPage[AuditEventOut]:
    """Return a cursor-paginated, filterable audit trail for a specific run.

    Scoped to the requesting tenant (T-M8-13: resource_id == run_id AND
    tenant_id == request.state.tenant_id).  Requires artifact:view permission
    (T-M8-14, REQ-M8-06).

    Cursor pagination: created_at < cursor ORDER BY created_at DESC LIMIT page_size.
    The cursor is an opaque ISO-8601 string encoding the last row's created_at.
    Pass nextCursor from the previous response to advance the page.
    """
    tenant_id = request.state.tenant_id

    # Base query: tenant-scoped + run-scoped (resource_id stores run_id per M8 plan)
    stmt = select(AuditEvent).where(
        AuditEvent.tenant_id == tenant_id,
        AuditEvent.resource_id == run_id,
    )

    # Optional filters
    if agent:
        # agent_type is stored inside the payload JSONB column
        stmt = stmt.where(AuditEvent.payload["agent_type"].astext == agent)
    if actor:
        stmt = stmt.where(AuditEvent.actor_id == actor)
    if event_type:
        stmt = stmt.where(AuditEvent.event_type == event_type)
    if since:
        since_dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
        stmt = stmt.where(AuditEvent.created_at >= since_dt)
    if until:
        until_dt = datetime.fromisoformat(until.replace("Z", "+00:00"))
        stmt = stmt.where(AuditEvent.created_at <= until_dt)

    # Cursor pagination: created_at < cursor_dt ORDER BY created_at DESC LIMIT page_size
    if cursor:
        cursor_dt = datetime.fromisoformat(cursor.replace("Z", "+00:00"))
        stmt = stmt.where(AuditEvent.created_at < cursor_dt)

    stmt = stmt.order_by(AuditEvent.created_at.desc()).limit(page_size)
    rows = (await db.execute(stmt)).scalars().all()

    items = [AuditEventOut.from_orm_audit(e) for e in rows]

    # Build next cursor from the last row's created_at (none if fewer rows than page_size)
    next_cursor: Optional[str] = None
    if len(rows) == page_size:
        last_ts = rows[-1].created_at
        if last_ts is not None:
            if last_ts.tzinfo is None:
                last_ts = last_ts.replace(tzinfo=timezone.utc)
            next_cursor = last_ts.isoformat()

    return CursorPage(items=items, nextCursor=next_cursor)
