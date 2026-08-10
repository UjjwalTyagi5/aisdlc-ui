"""Per-turn agent prompt overrides + a TTL-cached Agent Profile resolver.

Mirrors the connector/MCP contextvar pattern in shared/tools/mcp_runtime.py: the
copilot turn loop (or any caller) calls set_prompt_override(agent_id, prompt)
before invoking an agent's graph; self-injecting agent nodes read it back via
get_prompt_override(agent_id) and fall back to their baked-in constant when it
returns None. clear_prompt_override() runs in the caller's finally block (see
prompt_override_scope below) so an override never survives past its turn.

The contextvar propagates automatically into async graph nodes scheduled in the
same asyncio task (LangGraph awaits node coroutines inline). WARNING: nodes that
hop to a different thread or process (e.g. via asyncio.to_thread or a
ThreadPoolExecutor) do NOT see this contextvar unless the caller explicitly
copies context with contextvars.copy_context() and runs the callable through it.

resolve_profile_cached wraps shared.services.agent_profile_store.resolve_profile
with a short in-process TTL cache so the copilot turn loop can re-resolve a
tenant/agent/workspace/project's ResolvedProfile on every turn without hitting
Postgres each time. invalidate_profile_cache lets the Phase 2 profile-publish
path evict stale entries the moment an admin changes a profile.
"""
from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import AsyncIterator, Optional

from shared.services.agent_profile_store import (
    ResolvedProfile,
    inject_prompt,
    resolve_profile,
)

logger = logging.getLogger(__name__)

_prompt_overrides_var: ContextVar[dict] = ContextVar(
    "active_prompt_overrides", default={}
)

# Module-level plain dict — safe without locking because this process runs a
# single asyncio event loop and cache reads/writes never await mid-operation.
_profile_cache: dict[tuple, tuple[float, ResolvedProfile]] = {}


def set_prompt_override(agent_id: str, prompt: str) -> None:
    """Copy-on-write update so concurrent tasks never mutate a shared dict."""
    current = _prompt_overrides_var.get()
    updated = dict(current)
    updated[agent_id] = prompt
    _prompt_overrides_var.set(updated)


def get_prompt_override(agent_id: str) -> Optional[str]:
    """None when unset -> caller falls back to its baked-in prompt constant."""
    return _prompt_overrides_var.get().get(agent_id)


def clear_prompt_override(agent_id: Optional[str] = None) -> None:
    """Clear one agent's override, or every override when agent_id is None."""
    if agent_id is None:
        _prompt_overrides_var.set({})
        return
    current = _prompt_overrides_var.get()
    if agent_id not in current:
        return
    updated = dict(current)
    del updated[agent_id]
    _prompt_overrides_var.set(updated)


@asynccontextmanager
async def prompt_override_scope(
    agent_id: str, prompt: Optional[str]
) -> AsyncIterator[None]:
    """Set on enter (no-op when prompt is falsy), always clear on exit.

    Guarantees an override never outlives the turn it was set for, even if the
    wrapped agent invocation raises.
    """
    if prompt:
        set_prompt_override(agent_id, prompt)
    try:
        yield
    finally:
        clear_prompt_override(agent_id)


async def resolve_profile_cached(
    tenant_id,
    agent_id,
    workspace_id=None,
    project_id=None,
    *,
    ttl: float = 45.0,
) -> ResolvedProfile:
    """resolve_profile(...) with a short TTL cache to spare Postgres per-turn hits."""
    key = (str(tenant_id), str(agent_id), str(workspace_id), str(project_id))
    now = time.monotonic()
    cached = _profile_cache.get(key)
    if cached is not None and cached[0] > now:
        return cached[1]

    try:
        profile = await resolve_profile(tenant_id, agent_id, workspace_id, project_id)
    except Exception as exc:
        # Fail-soft: same empty profile resolve_profile itself returns on error.
        logger.warning(
            "resolve_profile_cached(%s/%s) failed: %s", tenant_id, agent_id, exc
        )
        profile = ResolvedProfile()

    _profile_cache[key] = (now + ttl, profile)
    return profile


def invalidate_profile_cache(
    tenant_id: Optional[str] = None, agent_id: Optional[str] = None
) -> None:
    """Drop cache entries matching the given filters; both None clears everything."""
    if tenant_id is None and agent_id is None:
        _profile_cache.clear()
        return
    for key in list(_profile_cache.keys()):
        cached_tenant, cached_agent, _, _ = key
        if tenant_id is not None and cached_tenant != str(tenant_id):
            continue
        if agent_id is not None and cached_agent != str(agent_id):
            continue
        _profile_cache.pop(key, None)


async def prepare_agent_turn(
    agent_id: str,
    base_prompt: Optional[str],
    tenant_id,
    project_id=None,
    workspace_id=None,
):
    """Compose one turn's prompt: profile + skills index over *base_prompt*.

    Resolves the workspace (from project_id when not given), the Agent Profile, and the
    active skills; builds the skills index; returns
    ``(injected_prompt, skills, profile)`` where:
      - injected_prompt: base with profile layers + skills index applied (or base
        unchanged when nothing to add / no tenant),
      - skills: the ResolvedSkill list to hand to skill_context_scope (may be []),
      - profile: the ResolvedProfile (for _stamp_profile_applied and the like).

    The skills index is layered even when the profile is empty — skills work without a
    published profile. Fail-soft: on any error returns ``(base_prompt, [], ResolvedProfile())``
    so a turn is never broken."""
    # Deferred import avoids a circular dependency (skill_runtime imports skill_store,
    # which does not import prompt_runtime, but keep the import local for symmetry).
    from shared.services.skill_runtime import resolve_skills_cached
    from shared.services.skill_store import build_skills_index

    empty_profile = ResolvedProfile()
    if not base_prompt or not tenant_id:
        return base_prompt, [], empty_profile
    try:
        if workspace_id is None and project_id is not None:
            workspace_id = await workspace_for_project(tenant_id, project_id)
        profile = await resolve_profile_cached(tenant_id, agent_id, workspace_id, project_id)
        skills = await resolve_skills_cached(tenant_id, agent_id, workspace_id, project_id)
        skills_index = build_skills_index(skills)
        injected = inject_prompt(base_prompt, profile, skills_index)
        return injected, skills, profile
    except Exception as exc:  # noqa: BLE001 — profile+skills are enhancements, never fatal
        logger.warning("prepare_agent_turn(%s) failed: %s — using base prompt", agent_id, exc)
        return base_prompt, [], empty_profile


async def workspace_for_project(
    tenant_id: Optional[str], project_id: Optional[str]
) -> Optional[str]:
    """Resolve a project's workspace_id for the agent-profile scope chain.

    The standalone agent APIs (each agent's own page/chat WS + REST) know their
    tenant and project but not the workspace; resolve_profile needs the workspace to
    apply workspace-scoped profile layers. Tenant-scoped read (RLS keeps it to the
    caller's org). Shared here — copilot keeps its own private `_workspace_for_project`
    untouched. Fail-soft None → profile resolution degrades to org(+project) scopes and
    a turn is never broken."""
    if not tenant_id or not project_id:
        return None
    try:
        import uuid as _uuid

        from sqlalchemy import select

        from shared.db import get_db_session_for_tenant
        from shared.models.orm import Project

        try:
            pid = _uuid.UUID(str(project_id))
        except (ValueError, TypeError, AttributeError):
            pid = project_id
        async with get_db_session_for_tenant(str(tenant_id)) as s:
            ws = (
                await s.execute(select(Project.workspace_id).where(Project.id == pid))
            ).scalar_one_or_none()
            return str(ws) if ws else None
    except Exception as exc:  # noqa: BLE001 — workspace resolution is an enhancement
        logger.warning("workspace_for_project(project=%s) failed: %s", project_id, exc)
        return None
