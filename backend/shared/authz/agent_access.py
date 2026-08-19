"""Which agents may this caller actually use, on this project.

`agent:invoke` (permissions.py) is a single blanket "may invoke agents at all" flag
every delivery role holds — it was never meant to distinguish Security from
Documentation, and nothing enforces it today (see multi-track-agent-access-design.md
§4.1). This module is that missing distinction: does THIS role reach THIS agent,
by default (AGENT_DEFAULT_REACH) or by an explicit project-scoped override
(agent_access_overrides, checked person-level first, then role-level)?

Deliberately never consults request.state.permissions or admin:* — Organization
Admin and Business Unit Admin hold zero agent access by design (spec §1.4). Resolving
the caller's role is delegated to effective_platform_role, which is itself
deliberately DB-backed (role_bindings), not JWT-trusted, for the same reason
grant_guard.py resolves permissions fresh rather than off the token.
"""
from __future__ import annotations

import uuid as _uuid

from fastapi import Depends, HTTPException, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from config.agent_registry import AGENT_DEFAULT_REACH
from shared.authz.effective_role import effective_platform_role
from shared.authz.project_scope import resolve_project
from shared.db import get_db_session


def _is_uuid(value: str) -> bool:
    try:
        _uuid.UUID(str(value))
        return True
    except (ValueError, AttributeError, TypeError):
        return False


async def check_agent_access(
    db: AsyncSession,
    *,
    tenant_id: str,
    project_id: str,
    role: str | None,
    user_id: str,
    agent_id: str,
) -> bool:
    """True if `role`/`user_id` may chat with and use `agent_id`'s Safe capabilities
    on `project_id`. Resolution order: person-level override -> role-level override ->
    the built-in default reach table -> deny."""
    if not project_id or not user_id:
        return False

    # `tenant_id`/`project_id` are genuine `uuid` columns on `agent_access_overrides`
    # (unlike `user_id` — see the comment below), so both must actually be UUID-shaped
    # before they reach the raw `CAST(... AS uuid)` SQL below. Mirrors
    # `project_scope.py`'s own `_is_uuid` guard: a real project route is slug-addressed
    # (`/security/{project_id}/...` where `{project_id}` is a slug like
    # "payments-portal"), so a caller reaching this function with a slug or other
    # garbage must fail closed (403 via `assert_agent_access`) rather than crash the
    # DB call with "invalid input syntax for type uuid" (a 500).
    if not tenant_id or not _is_uuid(tenant_id) or not _is_uuid(project_id):
        return False

    # user_id (agent_access_overrides, users.id) is a String(255) column, NOT uuid
    # (migration 0025 adds it as sa.String, matching users.id's own type) — unlike
    # tenant_id/project_id, which really are uuid columns. Casting :u to uuid here
    # would compare a uuid literal against a varchar column and fail at the DB with
    # "operator does not exist: character varying = uuid".
    person_row = (
        await db.execute(
            text(
                "SELECT involvement FROM agent_access_overrides "
                "WHERE tenant_id = CAST(:t AS uuid) AND project_id = CAST(:p AS uuid) "
                "  AND user_id = :u AND phase = :a"
            ),
            {"t": tenant_id, "p": project_id, "u": user_id, "a": agent_id},
        )
    ).first()
    if person_row is not None:
        return person_row.involvement != "none"

    if role:
        role_row = (
            await db.execute(
                text(
                    "SELECT involvement FROM agent_access_overrides "
                    "WHERE tenant_id = CAST(:t AS uuid) AND project_id = CAST(:p AS uuid) "
                    "  AND role = :r AND phase = :a"
                ),
                {"t": tenant_id, "p": project_id, "r": role, "a": agent_id},
            )
        ).first()
        if role_row is not None:
            return role_row.involvement != "none"

    default = AGENT_DEFAULT_REACH.get(agent_id, {}).get(role or "", "none")
    return default != "none"


async def assert_agent_access(
    db: AsyncSession,
    *,
    tenant_id: str,
    project_id: str,
    role: str | None,
    user_id: str,
    agent_id: str,
) -> None:
    """`check_agent_access`, raising 403 on denial. The direct call site for routes
    (like Security's REST/WS routes — see Tasks 6-7) that have no `{project_id}` path
    parameter for `require_agent_access` to read."""
    allowed = await check_agent_access(
        db, tenant_id=tenant_id, project_id=project_id,
        role=role, user_id=user_id, agent_id=agent_id,
    )
    if not allowed:
        raise HTTPException(
            status_code=403,
            detail=f"You don't have access to the {agent_id} agent on this project.",
        )


def require_agent_access(agent_id: str, project_id_param: str = "project_id"):
    """Router-level dependency enforcing `check_agent_access` on `{project_id_param}`.

    Mirrors `require_project_access`'s exact shape (Request + Depends(get_db_session),
    project id read from the path, `resolve_project` used to turn a UUID *or a slug*
    into a real project row before anything is checked). A route with no
    `{project_id_param}` path parameter passes through untouched, matching
    `require_project_access`'s own no-project-in-path behavior — there is nothing to
    scope to.
    """

    async def _dep(
        request: Request, db: AsyncSession = Depends(get_db_session)
    ) -> None:
        project_id_raw = request.path_params.get(project_id_param)
        if not project_id_raw:
            return
        tenant_id = getattr(request.state, "tenant_id", "") or ""
        if not tenant_id:
            raise HTTPException(status_code=403, detail="Forbidden")
        user_id = getattr(request.state, "user_id", "") or ""

        # `{project_id_param}` is a path segment, not necessarily a UUID — real
        # project routes in this codebase are slug-addressed (e.g.
        # `/dev/payments-portal/...`, see `project_scope.py`'s own docstring).
        # `resolve_project` is the same UUID-or-slug lookup `require_project_access`
        # already uses, so `assert_agent_access` below always receives a genuine
        # project UUID regardless of how the caller addressed the route.
        project = await resolve_project(db, str(tenant_id), project_id_raw)
        if project is None:
            raise HTTPException(status_code=404, detail="not found")

        role = await effective_platform_role(db, request)
        await assert_agent_access(
            db, tenant_id=str(tenant_id), project_id=str(project.id),
            role=role, user_id=str(user_id), agent_id=agent_id,
        )

    _dep.__rbac_require_permission__ = True  # D-05 boot-scan sentinel
    return _dep
