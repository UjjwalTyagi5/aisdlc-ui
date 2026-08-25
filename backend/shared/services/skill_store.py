"""Skill resolution + management store (Phase 4 skills platform).

Vendor skills live read-only on disk (shared.skills.registry). Custom (org-authored)
skill content and per-scope enable/disable toggles are persisted in Postgres
(AgentSkill / AgentSkillToggle, tenant-scoped under FORCE RLS), mirroring the shape
of agent_profile_store.

Two consumers:
  - RUNTIME (resolve_active_skills / build_skills_index): what an agent turn sees —
    vendor + enabled custom skills merged across the org→workspace→project scope chain,
    with project shadowing workspace shadowing org. Fail-soft: returns [] on any error so
    a turn is never broken.
  - MANAGEMENT (list_skills_merged / get_skill_detail / create/update/soft_delete/
    versions/activate/set_skill_enabled): the CRUD the skills router drives. Write helpers
    raise ValueError for not-found so the router can map to 404.

Scope chain / precedence: project > workspace > org (later scope shadows the same
skill_key; toggles at a nearer scope win). org rows carry scope_id = NULL.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select

from shared.db import get_db_session_for_tenant
from shared.models.orm import AgentSkill, AgentSkillToggle
from shared.skills.registry import vendor_skills_for

logger = logging.getLogger(__name__)

_SCOPE_RANK = {"org": 0, "workspace": 1, "project": 2}


def _toggle_precedence(
    toggle_rows_with_rank: list[tuple[int, "AgentSkillToggle"]],
) -> dict[tuple[str, str], bool]:
    """(origin, skill_key) -> effective enabled, nearest scope (highest rank) wins.
    Shared shape with resolve_active_skills' own nearest-wins toggle logic, but kept
    as a separate small helper here rather than refactored to share code with that
    function — resolve_active_skills is on the RUNTIME path (every agent turn) and
    has no test coverage of its own yet; extending its signature to serve this
    management-list use case is a bigger, riskier change than this feature needs.
    """
    best: dict[tuple[str, str], tuple[int, bool]] = {}
    for rank, t in toggle_rows_with_rank:
        k = (t.origin, t.skill_key)
        cur = best.get(k)
        if cur is None or rank > cur[0]:
            best[k] = (rank, bool(t.enabled))
    return {k: v[1] for k, v in best.items()}


_INDEX_HEADER = (
    "AVAILABLE SKILLS — call load_skill(\"<key>\") to load full instructions "
    "BEFORE applying one:"
)
_INDEX_CAP = 1500


@dataclass
class ResolvedSkill:
    skill_key: str
    agent_id: str
    origin: str  # 'vendor' | 'custom'
    display_name: str
    description: str
    when_to_use: str
    body: str
    runtime: str


# ── helpers ─────────────────────────────────────────────────────────────────────

def _as_uuid(value) -> Optional[uuid.UUID]:
    if value is None:
        return None
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return None


def _applies(row_scope: str, row_scope_id, workspace_id, project_id) -> bool:
    if row_scope == "org":
        return True
    if row_scope == "workspace":
        return workspace_id is not None and str(row_scope_id) == str(workspace_id)
    if row_scope == "project":
        return project_id is not None and str(row_scope_id) == str(project_id)
    return False


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt else None


# ── RUNTIME resolution ────────────────────────────────────────────────────────────

async def resolve_active_skills(
    tenant_id,
    agent_id,
    workspace_id=None,
    project_id=None,
) -> list[ResolvedSkill]:
    """Vendor + enabled-custom skills for this agent, merged across the scope chain.

    Fail-soft: returns [] on any error (identical degradation to resolve_profile)."""
    if not tenant_id:
        return []
    try:
        async with get_db_session_for_tenant(str(tenant_id)) as session:
            custom_rows = list((await session.execute(
                select(AgentSkill).where(
                    AgentSkill.agent_id == str(agent_id),
                    AgentSkill.is_active.is_(True),
                    AgentSkill.deleted_at.is_(None),
                )
            )).scalars().all())
            toggle_rows = list((await session.execute(
                select(AgentSkillToggle).where(
                    AgentSkillToggle.agent_id == str(agent_id),
                )
            )).scalars().all())
    except Exception as exc:  # noqa: BLE001 — skills are an enhancement, never fatal
        logger.warning("resolve_active_skills(%s/%s) failed: %s", tenant_id, agent_id, exc)
        return []

    # Toggle precedence: nearest applicable scope wins (project > workspace > org).
    toggle_by_key: dict[tuple, tuple[int, bool]] = {}
    for t in toggle_rows:
        if not _applies(t.scope, t.scope_id, workspace_id, project_id):
            continue
        rank = _SCOPE_RANK.get(t.scope, -1)
        k = (t.origin, t.skill_key)
        cur = toggle_by_key.get(k)
        if cur is None or rank > cur[0]:
            toggle_by_key[k] = (rank, bool(t.enabled))

    def _enabled(origin: str, skill_key: str) -> bool:
        hit = toggle_by_key.get((origin, skill_key))
        if hit is not None:
            return hit[1]
        return True  # default ON — vendor always, custom because it's active & not deleted

    out: list[ResolvedSkill] = []

    # Custom rows: keep the nearest applicable scope per skill_key (project shadows org).
    best_custom: dict[str, tuple[int, AgentSkill]] = {}
    for r in custom_rows:
        if not _applies(r.scope, r.scope_id, workspace_id, project_id):
            continue
        rank = _SCOPE_RANK.get(r.scope, -1)
        cur = best_custom.get(r.skill_key)
        if cur is None or rank > cur[0]:
            best_custom[r.skill_key] = (rank, r)

    shadowed = set(best_custom.keys())

    for skill_key, (_, r) in best_custom.items():
        if not _enabled("custom", skill_key):
            continue
        out.append(ResolvedSkill(
            skill_key=r.skill_key,
            agent_id=r.agent_id,
            origin="custom",
            display_name=r.display_name or r.skill_key,
            description=r.description or "",
            when_to_use=r.when_to_use or "",
            body=r.body or "",
            runtime=r.runtime or "llm",
        ))

    for v in vendor_skills_for(str(agent_id)):
        if v.skill_key in shadowed:  # a custom skill of the same key overrides the vendor one
            continue
        if not _enabled("vendor", v.skill_key):
            continue
        out.append(ResolvedSkill(
            skill_key=v.skill_key,
            agent_id=v.agent_id,
            origin="vendor",
            display_name=v.display_name,
            description=v.description,
            when_to_use=v.when_to_use,
            body=v.body,
            runtime=v.runtime,
        ))

    out.sort(key=lambda s: s.display_name.lower())
    return out


def build_skills_index(skills: "list[ResolvedSkill]") -> str:
    """Compact catalog string for the system prompt. '' when empty, capped ~1500 chars."""
    if not skills:
        return ""
    lines = [_INDEX_HEADER]
    included = 0
    for s in skills:
        when = f" (use when: {s.when_to_use})" if s.when_to_use else ""
        line = f"- {s.skill_key}: {s.description}{when}"
        candidate = "\n".join(lines + [line])
        remaining = len(skills) - included
        if len(candidate) > _INDEX_CAP:
            lines.append(f"…and {remaining} more")
            break
        lines.append(line)
        included += 1
    return "\n".join(lines)


# ── MANAGEMENT: read ────────────────────────────────────────────────────────────

def _list_item(
    *, origin: str, skill_key: str, agent_id: str, display_name: str,
    description: str, when_to_use: str, runtime: str, enabled: bool,
    version: Optional[int], active_version: Optional[int],
    origin_scope: Optional[str] = None, requested_scope: Optional[str] = None,
) -> dict:
    """`origin_scope`: which tier this item's content actually lives at (None for
    vendor — it has no scope of its own). `requested_scope`: the tier the caller
    asked about, used only to decide editable/deletable — a custom item whose
    origin_scope differs from what was asked (an INHERITED item) is not editable
    or deletable at the asked tier, only overridable; update/delete are exact-scope
    operations and would 404 against an ancestor's row."""
    return {
        "origin": origin,
        "skill_key": skill_key,
        "agent_id": agent_id,
        "display_name": display_name,
        "description": description,
        "when_to_use": when_to_use,
        "runtime": runtime,
        "enabled": enabled,
        "editable": origin == "custom" and origin_scope == requested_scope,
        "deletable": origin == "custom" and origin_scope == requested_scope,
        "version": version,
        "active_version": active_version,
        "origin_scope": origin_scope,
    }


async def list_skills_merged(tenant_id, agent_id, scope, scope_id, ancestor=None) -> list[dict]:
    """Management list for one scope: vendor skills + custom skills authored here OR
    inherited from an ancestor tier (nearest wins per skill_key), each with its
    effective enabled flag (nearest applicable toggle wins, own scope included).
    Fail-soft to [].

    `ancestor`: nearest-first [(scope, scope_id), ...] above `scope` — see
    shared.routers.agent_profiles.ancestor_chain. None/[] (default) matches today's
    exact behavior: no ancestor tiers are consulted at all.
    """
    if not tenant_id:
        return []
    ancestor = ancestor or []
    sid = _as_uuid(scope_id) if scope != "org" else None
    try:
        async with get_db_session_for_tenant(str(tenant_id)) as session:
            custom_rows = list((await session.execute(
                select(AgentSkill).where(
                    AgentSkill.agent_id == str(agent_id),
                    AgentSkill.scope == scope,
                    AgentSkill.scope_id.is_(None) if sid is None else AgentSkill.scope_id == sid,
                    AgentSkill.deleted_at.is_(None),
                )
            )).scalars().all())
            toggle_rows = list((await session.execute(
                select(AgentSkillToggle).where(
                    AgentSkillToggle.agent_id == str(agent_id),
                    AgentSkillToggle.scope == scope,
                    AgentSkillToggle.scope_id.is_(None) if sid is None else AgentSkillToggle.scope_id == sid,
                )
            )).scalars().all())

            ancestor_custom: list[tuple[str, AgentSkill]] = []
            ancestor_toggle_rows: list[tuple[str, AgentSkillToggle]] = []
            for anc_scope, anc_scope_id in ancestor:
                anc_sid = _as_uuid(anc_scope_id) if anc_scope != "org" else None
                anc_custom_rows = list((await session.execute(
                    select(AgentSkill).where(
                        AgentSkill.agent_id == str(agent_id),
                        AgentSkill.scope == anc_scope,
                        AgentSkill.scope_id.is_(None) if anc_sid is None else AgentSkill.scope_id == anc_sid,
                        AgentSkill.deleted_at.is_(None),
                        AgentSkill.is_active.is_(True),
                    )
                )).scalars().all())
                ancestor_custom.extend((anc_scope, r) for r in anc_custom_rows)
                anc_toggle_rows = list((await session.execute(
                    select(AgentSkillToggle).where(
                        AgentSkillToggle.agent_id == str(agent_id),
                        AgentSkillToggle.scope == anc_scope,
                        AgentSkillToggle.scope_id.is_(None) if anc_sid is None else AgentSkillToggle.scope_id == anc_sid,
                    )
                )).scalars().all())
                ancestor_toggle_rows.extend((anc_scope, t) for t in anc_toggle_rows)
    except Exception as exc:  # noqa: BLE001
        logger.warning("list_skills_merged(%s/%s) failed: %s", tenant_id, agent_id, exc)
        return []

    own_rank = _SCOPE_RANK.get(scope, 0)
    toggle_ranked = [(own_rank, t) for t in toggle_rows] + [
        (_SCOPE_RANK.get(anc_scope, -1), t) for anc_scope, t in ancestor_toggle_rows
    ]
    enabled_by_key = _toggle_precedence(toggle_ranked)

    items: list[dict] = []

    for v in vendor_skills_for(str(agent_id)):
        items.append(_list_item(
            origin="vendor", skill_key=v.skill_key, agent_id=v.agent_id,
            display_name=v.display_name, description=v.description,
            when_to_use=v.when_to_use, runtime=v.runtime,
            enabled=enabled_by_key.get(("vendor", v.skill_key), True),
            version=None, active_version=None,
            origin_scope=None, requested_scope=scope,
        ))

    # Own scope's active custom rows, tagged with this scope; then ancestor rows for
    # any skill_key not already claimed by the own scope (nearest-first order already
    # guaranteed by the caller's `ancestor` argument, so first-inserted-per-key wins).
    active_by_key: dict[str, tuple[str, AgentSkill]] = {}
    for r in custom_rows:
        if r.is_active:
            active_by_key[r.skill_key] = (scope, r)
    for anc_scope, r in ancestor_custom:
        if r.skill_key not in active_by_key:
            active_by_key[r.skill_key] = (anc_scope, r)

    for skill_key, (origin_scope, r) in active_by_key.items():
        items.append(_list_item(
            origin="custom", skill_key=r.skill_key, agent_id=r.agent_id,
            display_name=r.display_name or r.skill_key, description=r.description or "",
            when_to_use=r.when_to_use or "", runtime=r.runtime or "llm",
            enabled=enabled_by_key.get(("custom", r.skill_key), True),
            version=r.version, active_version=r.version,
            origin_scope=origin_scope, requested_scope=scope,
        ))

    items.sort(key=lambda i: (i["origin"] != "custom", i["display_name"].lower()))
    return items


async def get_skill_detail(
    tenant_id, agent_id, scope, scope_id, origin, skill_key
) -> Optional[dict]:
    """Full detail (incl. body) for one skill at one scope, or None if absent."""
    if not tenant_id:
        return None
    if origin == "vendor":
        for v in vendor_skills_for(str(agent_id)):
            if v.skill_key == skill_key:
                enabled = True
                try:
                    async with get_db_session_for_tenant(str(tenant_id)) as session:
                        t = await _fetch_toggle(session, agent_id, scope, scope_id, "vendor", skill_key)
                        if t is not None:
                            enabled = bool(t.enabled)
                except Exception:  # noqa: BLE001
                    pass
                item = _list_item(
                    origin="vendor", skill_key=v.skill_key, agent_id=v.agent_id,
                    display_name=v.display_name, description=v.description,
                    when_to_use=v.when_to_use, runtime=v.runtime, enabled=enabled,
                    version=None, active_version=None,
                    origin_scope=None, requested_scope=scope,
                )
                item.update({"body": v.body, "created_by": None,
                             "created_at": None, "updated_at": None})
                return item
        return None

    sid = _as_uuid(scope_id) if scope != "org" else None
    try:
        async with get_db_session_for_tenant(str(tenant_id)) as session:
            row = (await session.execute(
                select(AgentSkill).where(
                    AgentSkill.agent_id == str(agent_id),
                    AgentSkill.scope == scope,
                    AgentSkill.scope_id.is_(None) if sid is None else AgentSkill.scope_id == sid,
                    AgentSkill.skill_key == skill_key,
                    AgentSkill.is_active.is_(True),
                    AgentSkill.deleted_at.is_(None),
                ).order_by(AgentSkill.version.desc())
            )).scalars().first()
            if row is None:
                return None
            t = await _fetch_toggle(session, agent_id, scope, scope_id, "custom", skill_key)
            enabled = bool(t.enabled) if t is not None else True
    except Exception as exc:  # noqa: BLE001
        logger.warning("get_skill_detail(%s/%s) failed: %s", tenant_id, agent_id, exc)
        return None

    item = _list_item(
        origin="custom", skill_key=row.skill_key, agent_id=row.agent_id,
        display_name=row.display_name or row.skill_key, description=row.description or "",
        when_to_use=row.when_to_use or "", runtime=row.runtime or "llm", enabled=enabled,
        version=row.version, active_version=row.version,
        # get_skill_detail only ever resolves an exact-scope row (it never walks
        # the ancestor chain itself — callers that need an inherited item's detail
        # pass its own origin_scope as `scope` directly, see agent_skills.py's
        # toggle_skill and the frontend's view/edit fetch), so a row found here
        # always lives at exactly the scope asked for.
        origin_scope=scope, requested_scope=scope,
    )
    item.update({
        "body": row.body or "",
        "created_by": row.created_by,
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    })
    return item


async def list_custom_versions(tenant_id, agent_id, scope, scope_id, skill_key) -> list[dict]:
    """All non-deleted versions of a custom skill at one scope, newest first."""
    if not tenant_id:
        return []
    sid = _as_uuid(scope_id) if scope != "org" else None
    try:
        async with get_db_session_for_tenant(str(tenant_id)) as session:
            rows = list((await session.execute(
                select(AgentSkill).where(
                    AgentSkill.agent_id == str(agent_id),
                    AgentSkill.scope == scope,
                    AgentSkill.scope_id.is_(None) if sid is None else AgentSkill.scope_id == sid,
                    AgentSkill.skill_key == skill_key,
                    AgentSkill.deleted_at.is_(None),
                ).order_by(AgentSkill.version.desc())
            )).scalars().all())
    except Exception as exc:  # noqa: BLE001
        logger.warning("list_custom_versions(%s/%s) failed: %s", tenant_id, agent_id, exc)
        return []
    return [{
        "version": r.version,
        "is_active": bool(r.is_active),
        "display_name": r.display_name or r.skill_key,
        "description": r.description or "",
        "when_to_use": r.when_to_use or "",
        "created_by": r.created_by,
        "created_at": _iso(r.created_at),
    } for r in rows]


# ── MANAGEMENT: write ─────────────────────────────────────────────────────────────

async def _fetch_toggle(session, agent_id, scope, scope_id, origin, skill_key):
    sid = _as_uuid(scope_id) if scope != "org" else None
    return (await session.execute(
        select(AgentSkillToggle).where(
            AgentSkillToggle.agent_id == str(agent_id),
            AgentSkillToggle.scope == scope,
            AgentSkillToggle.scope_id.is_(None) if sid is None else AgentSkillToggle.scope_id == sid,
            AgentSkillToggle.origin == origin,
            AgentSkillToggle.skill_key == skill_key,
        )
    )).scalars().first()


async def create_custom_skill(
    tenant_id, agent_id, scope, scope_id, skill_key, display_name,
    description, when_to_use, body, created_by,
) -> dict:
    """Insert a v1 active custom skill. Raises ValueError if one already exists."""
    sid = _as_uuid(scope_id) if scope != "org" else None
    async with get_db_session_for_tenant(str(tenant_id)) as session:
        existing = (await session.execute(
            select(AgentSkill).where(
                AgentSkill.agent_id == str(agent_id),
                AgentSkill.scope == scope,
                AgentSkill.scope_id.is_(None) if sid is None else AgentSkill.scope_id == sid,
                AgentSkill.skill_key == skill_key,
                AgentSkill.deleted_at.is_(None),
            )
        )).scalars().first()
        if existing is not None:
            raise ValueError(f"skill '{skill_key}' already exists at {scope} scope")
        row = AgentSkill(
            tenant_id=_as_uuid(tenant_id),
            agent_id=str(agent_id),
            scope=scope,
            scope_id=sid,
            skill_key=skill_key,
            version=1,
            is_active=True,
            display_name=display_name or skill_key,
            description=description,
            when_to_use=when_to_use,
            body=body,
            runtime="llm",
            origin="custom",
            created_by=created_by or "system",
        )
        session.add(row)
        await session.flush()
        version = row.version
    detail = await get_skill_detail(tenant_id, agent_id, scope, scope_id, "custom", skill_key)
    return detail or {"skill_key": skill_key, "version": version, "origin": "custom"}


async def update_custom_skill(
    tenant_id, agent_id, scope, scope_id, skill_key, display_name,
    description, when_to_use, body, created_by,
) -> Optional[dict]:
    """Insert v(n+1) and atomically flip the active flag. None when no existing skill."""
    sid = _as_uuid(scope_id) if scope != "org" else None
    async with get_db_session_for_tenant(str(tenant_id)) as session:
        rows = list((await session.execute(
            select(AgentSkill).where(
                AgentSkill.agent_id == str(agent_id),
                AgentSkill.scope == scope,
                AgentSkill.scope_id.is_(None) if sid is None else AgentSkill.scope_id == sid,
                AgentSkill.skill_key == skill_key,
                AgentSkill.deleted_at.is_(None),
            ).order_by(AgentSkill.version.desc())
        )).scalars().all())
        if not rows:
            return None
        next_version = rows[0].version + 1
        for r in rows:
            if r.is_active:
                r.is_active = False
        new_row = AgentSkill(
            tenant_id=_as_uuid(tenant_id),
            agent_id=str(agent_id),
            scope=scope,
            scope_id=sid,
            skill_key=skill_key,
            version=next_version,
            is_active=True,
            display_name=display_name or skill_key,
            description=description,
            when_to_use=when_to_use,
            body=body,
            runtime="llm",
            origin="custom",
            created_by=created_by or "system",
        )
        session.add(new_row)
        await session.flush()
    return await get_skill_detail(tenant_id, agent_id, scope, scope_id, "custom", skill_key)


async def soft_delete_custom_skill(tenant_id, agent_id, scope, scope_id, skill_key) -> bool:
    """Mark all live versions deleted. True if anything was deleted."""
    sid = _as_uuid(scope_id) if scope != "org" else None
    now = datetime.now(timezone.utc)
    async with get_db_session_for_tenant(str(tenant_id)) as session:
        rows = list((await session.execute(
            select(AgentSkill).where(
                AgentSkill.agent_id == str(agent_id),
                AgentSkill.scope == scope,
                AgentSkill.scope_id.is_(None) if sid is None else AgentSkill.scope_id == sid,
                AgentSkill.skill_key == skill_key,
                AgentSkill.deleted_at.is_(None),
            )
        )).scalars().all())
        if not rows:
            return False
        for r in rows:
            r.deleted_at = now
            r.is_active = False
        await session.flush()
    return True


async def activate_custom_version(
    tenant_id, agent_id, scope, scope_id, skill_key, version
) -> Optional[dict]:
    """Flip the active flag to the given version. None when that version is absent."""
    sid = _as_uuid(scope_id) if scope != "org" else None
    async with get_db_session_for_tenant(str(tenant_id)) as session:
        rows = list((await session.execute(
            select(AgentSkill).where(
                AgentSkill.agent_id == str(agent_id),
                AgentSkill.scope == scope,
                AgentSkill.scope_id.is_(None) if sid is None else AgentSkill.scope_id == sid,
                AgentSkill.skill_key == skill_key,
                AgentSkill.deleted_at.is_(None),
            )
        )).scalars().all())
        target = next((r for r in rows if r.version == int(version)), None)
        if target is None:
            return None
        for r in rows:
            r.is_active = (r.version == int(version))
        await session.flush()
    return await get_skill_detail(tenant_id, agent_id, scope, scope_id, "custom", skill_key)


async def set_skill_enabled(
    tenant_id, agent_id, scope, scope_id, origin, skill_key, enabled, updated_by
) -> None:
    """Upsert the per-scope enable/disable toggle for a vendor or custom skill."""
    sid = _as_uuid(scope_id) if scope != "org" else None
    async with get_db_session_for_tenant(str(tenant_id)) as session:
        row = await _fetch_toggle(session, agent_id, scope, scope_id, origin, skill_key)
        if row is None:
            row = AgentSkillToggle(
                tenant_id=_as_uuid(tenant_id),
                agent_id=str(agent_id),
                scope=scope,
                scope_id=sid,
                origin=origin,
                skill_key=skill_key,
                enabled=bool(enabled),
                updated_by=updated_by or "system",
            )
            session.add(row)
        else:
            row.enabled = bool(enabled)
            row.updated_by = updated_by or "system"
        await session.flush()
