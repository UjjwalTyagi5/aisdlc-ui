"""Tech stacks in Postgres — the only module that reads or writes the two tables.

Every call is tenant-scoped (`get_db_session_for_tenant` sets the RLS tenant), so another
organisation's rows are invisible rather than filtered. Authorisation is not here: the router
decides who may write; this module does what it is told.

THE RESOLVER IS ON EVERY DESIGN TURN, so it is one connection and three queries, cached for
45 s per (tenant, project) — the same budget skills use — and every write invalidates the
tenant's entries, so an admin's change applies to the next turn.
"""
from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import and_, func, or_, select, update

from shared.db import get_db_session_for_tenant
from shared.models.orm import Project, ProjectTechStackSelection, TechStackRecord, Workspace
from shared.services.tech_stack import EffectiveTechStack, TechStack, decide_effective

logger = logging.getLogger(__name__)

TTL_SECONDS = 45.0
_CACHE: dict[tuple[str, str], tuple[float, EffectiveTechStack]] = {}


class DuplicateStackName(ValueError):
    """A live stack of the same tier already has this name."""


def _uuid(value) -> Optional[uuid.UUID]:
    if value is None:
        return None
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        return None


def _to_stack(row: TechStackRecord) -> TechStack:
    return TechStack(
        id=str(row.id), scope=row.scope,
        workspace_id=str(row.workspace_id) if row.workspace_id else None,
        project_id=str(row.project_id) if row.project_id else None,
        name=row.name, description=row.description or "", categories=dict(row.categories or {}),
        notes=row.notes or "", is_default=bool(row.is_default), deleted=row.deleted_at is not None,
    )


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── reads ───────────────────────────────────────────────────────────────────────

async def project_scope(tenant_id, project_id) -> tuple[bool, Optional[str]]:
    """(the project exists in this tenant, its Business Unit id or None)."""
    pid = _uuid(project_id)
    if pid is None:
        return False, None
    async with get_db_session_for_tenant(str(tenant_id)) as db:
        row = (await db.execute(select(Project.id, Project.workspace_id).where(Project.id == pid))).first()
    if row is None:
        return False, None
    return True, (str(row.workspace_id) if row.workspace_id else None)


async def workspace_exists(tenant_id, workspace_id) -> bool:
    wid = _uuid(workspace_id)
    if wid is None:
        return False
    async with get_db_session_for_tenant(str(tenant_id)) as db:
        return (await db.execute(select(Workspace.id).where(Workspace.id == wid))).first() is not None


def _live_ordered(where):
    return (select(TechStackRecord).where(where, TechStackRecord.deleted_at.is_(None))
            .order_by(TechStackRecord.is_default.desc(), func.lower(TechStackRecord.name)))


async def list_workspace_stacks(tenant_id, workspace_id) -> list[TechStack]:
    wid = _uuid(workspace_id)
    if wid is None:
        return []
    async with get_db_session_for_tenant(str(tenant_id)) as db:
        rows = (await db.execute(_live_ordered(TechStackRecord.workspace_id == wid))).scalars().all()
    return [_to_stack(r) for r in rows]


async def list_project_stacks(tenant_id, project_id) -> list[TechStack]:
    pid = _uuid(project_id)
    if pid is None:
        return []
    async with get_db_session_for_tenant(str(tenant_id)) as db:
        rows = (await db.execute(_live_ordered(TechStackRecord.project_id == pid))).scalars().all()
    return [_to_stack(r) for r in rows]


async def get_stack(tenant_id, stack_id, *, include_deleted: bool = False) -> Optional[TechStack]:
    sid = _uuid(stack_id)
    if sid is None:
        return None
    async with get_db_session_for_tenant(str(tenant_id)) as db:
        row = (await db.execute(select(TechStackRecord).where(TechStackRecord.id == sid))).scalar_one_or_none()
    if row is None or (row.deleted_at is not None and not include_deleted):
        return None
    return _to_stack(row)


async def get_selection(tenant_id, project_id) -> Optional[str]:
    pid = _uuid(project_id)
    if pid is None:
        return None
    async with get_db_session_for_tenant(str(tenant_id)) as db:
        sid = (await db.execute(select(ProjectTechStackSelection.tech_stack_id).where(
            ProjectTechStackSelection.project_id == pid))).scalar_one_or_none()
    return str(sid) if sid else None


# ── writes ──────────────────────────────────────────────────────────────────────

async def _name_taken(db, scope: str, scope_uuid, name: str, exclude=None) -> bool:
    column = TechStackRecord.workspace_id if scope == "workspace" else TechStackRecord.project_id
    q = select(TechStackRecord.id).where(column == scope_uuid, TechStackRecord.deleted_at.is_(None),
                                         func.lower(TechStackRecord.name) == name.lower())
    if exclude is not None:
        q = q.where(TechStackRecord.id != exclude)
    return (await db.execute(q.limit(1))).first() is not None


async def create_stack(tenant_id, *, scope: str, scope_id, fields: dict, actor: str) -> TechStack:
    sid = _uuid(scope_id)
    async with get_db_session_for_tenant(str(tenant_id)) as db:
        if await _name_taken(db, scope, sid, fields["name"]):
            raise DuplicateStackName(fields["name"])
        now = _now()
        row = TechStackRecord(
            id=uuid.uuid4(), tenant_id=_uuid(tenant_id), scope=scope,
            workspace_id=sid if scope == "workspace" else None,
            project_id=sid if scope == "project" else None,
            name=fields["name"], description=fields["description"], categories=fields["categories"],
            notes=fields["notes"], is_default=False, created_by=actor, updated_by=actor,
            created_at=now, updated_at=now,
        )
        db.add(row)
        await db.flush()
        return _to_stack(row)


async def _live_row(db, stack_id) -> Optional[TechStackRecord]:
    sid = _uuid(stack_id)
    if sid is None:
        return None
    return (await db.execute(select(TechStackRecord).where(
        TechStackRecord.id == sid, TechStackRecord.deleted_at.is_(None)))).scalar_one_or_none()


async def update_stack(tenant_id, stack_id, fields: dict, actor: str) -> Optional[TechStack]:
    async with get_db_session_for_tenant(str(tenant_id)) as db:
        row = await _live_row(db, stack_id)
        if row is None:
            return None
        scope_uuid = row.workspace_id if row.scope == "workspace" else row.project_id
        if await _name_taken(db, row.scope, scope_uuid, fields["name"], exclude=row.id):
            raise DuplicateStackName(fields["name"])
        row.name, row.description = fields["name"], fields["description"]
        row.categories, row.notes = fields["categories"], fields["notes"]
        row.updated_by, row.updated_at = actor, _now()
        await db.flush()
        return _to_stack(row)


async def delete_stack(tenant_id, stack_id, actor: str) -> Optional[TechStack]:
    """Soft: the row stays, so a project that chose it can be told it was deleted."""
    async with get_db_session_for_tenant(str(tenant_id)) as db:
        row = await _live_row(db, stack_id)
        if row is None:
            return None
        now = _now()
        row.deleted_at, row.is_default = now, False
        row.updated_by, row.updated_at = actor, now
        await db.flush()
        return _to_stack(row)


async def set_default(tenant_id, stack_id, is_default: bool, actor: str) -> Optional[TechStack]:
    """Make this Business Unit stack the BU's default (clearing any other), or clear it."""
    async with get_db_session_for_tenant(str(tenant_id)) as db:
        row = await _live_row(db, stack_id)
        if row is None or row.scope != "workspace":
            return None
        now = _now()
        if is_default:
            # Clear the old default FIRST, so the one-default-per-BU index never sees two.
            await db.execute(update(TechStackRecord).where(
                TechStackRecord.workspace_id == row.workspace_id, TechStackRecord.is_default.is_(True),
                TechStackRecord.id != row.id).values(is_default=False, updated_by=actor, updated_at=now))
            await db.flush()
        row.is_default, row.updated_by, row.updated_at = is_default, actor, now
        await db.flush()
        return _to_stack(row)


async def set_selection(tenant_id, project_id, stack_id: Optional[str], actor: str) -> None:
    """Point the project at a stack, or (None) back at its Business Unit default."""
    pid = _uuid(project_id)
    async with get_db_session_for_tenant(str(tenant_id)) as db:
        existing = await db.get(ProjectTechStackSelection, pid)
        if stack_id is None:
            if existing is not None:
                await db.delete(existing)
            return
        if existing is None:
            db.add(ProjectTechStackSelection(project_id=pid, tenant_id=_uuid(tenant_id),
                                             tech_stack_id=_uuid(stack_id), selected_by=actor,
                                             selected_at=_now()))
        else:
            existing.tech_stack_id, existing.selected_by, existing.selected_at = _uuid(stack_id), actor, _now()


# ── what agents follow ──────────────────────────────────────────────────────────

async def resolve_project_tech_stack(tenant_id, project_id) -> EffectiveTechStack:
    """The stack this project follows now — one connection, three queries."""
    pid = _uuid(project_id)
    if not tenant_id or pid is None:
        return EffectiveTechStack(None, "none")
    async with get_db_session_for_tenant(str(tenant_id)) as db:
        ws = (await db.execute(select(Project.workspace_id).where(Project.id == pid))).scalar_one_or_none()
        sel_id = (await db.execute(select(ProjectTechStackSelection.tech_stack_id).where(
            ProjectTechStackSelection.project_id == pid))).scalar_one_or_none()
        conditions = []
        if sel_id is not None:
            conditions.append(TechStackRecord.id == sel_id)
        if ws is not None:
            conditions.append(and_(TechStackRecord.workspace_id == ws, TechStackRecord.is_default.is_(True),
                                   TechStackRecord.deleted_at.is_(None)))
        rows = (await db.execute(select(TechStackRecord).where(or_(*conditions)))).scalars().all() if conditions else []
    selected = next((_to_stack(r) for r in rows if sel_id is not None and r.id == sel_id), None)
    default_row = next((r for r in rows if ws is not None and r.workspace_id == ws and r.is_default
                        and r.deleted_at is None), None)
    return decide_effective(project_id=str(pid), workspace_id=str(ws) if ws else None,
                            selected=selected, selection_id=str(sel_id) if sel_id else None,
                            default=_to_stack(default_row) if default_row else None)


async def resolve_project_tech_stack_cached(tenant_id, project_id, *, ttl: float = TTL_SECONDS) -> EffectiveTechStack:
    key = (str(tenant_id), str(project_id))
    now = time.monotonic()
    hit = _CACHE.get(key)
    if hit is not None and hit[0] > now:
        return hit[1]
    try:
        eff = await resolve_project_tech_stack(tenant_id, project_id)
    except Exception as exc:  # noqa: BLE001 — never cached, never silent: the warning reaches the user
        logger.warning("tech stack resolution failed for project %s: %s", project_id, type(exc).__name__)
        return EffectiveTechStack(None, "none",
                                  "The project's tech stack could not be read, so this answer does not follow one.")
    _CACHE[key] = (now + ttl, eff)
    return eff


def invalidate_tech_stack_cache(tenant_id=None) -> None:
    for key in [k for k in _CACHE if tenant_id is None or k[0] == str(tenant_id)]:
        _CACHE.pop(key, None)


async def current_project_tech_stack() -> Optional[EffectiveTechStack]:
    """The stack for the turn's project (tenant and project from `config.ws_helper`, which every
    Design entry point sets), or None when the turn has no project."""
    from config.ws_helper import get_project_id, get_tenant_id  # noqa: PLC0415

    tenant, project = get_tenant_id(), get_project_id()
    if not tenant or not project:
        return None
    return await resolve_project_tech_stack_cached(tenant, project)
