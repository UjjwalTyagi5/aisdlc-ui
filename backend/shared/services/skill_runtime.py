"""Per-turn skill channel: a contextvar of loaded skills + a single load_skill tool.

Mirrors shared/tools/mcp_runtime.py and shared/services/prompt_runtime.py exactly. The
turn loop (copilot activity or a standalone agent API) resolves the run's active skills
and enters skill_context_scope(agent_id, skills) before invoking the graph; the agent
node binds base_tools + get_mcp_tools() + get_skill_tools(agent_id), and the ONE
load_skill tool reads a skill body straight out of the contextvar (no DB access). The
scope clears the contextvar in finally so a skill set never survives its turn.

The contextvar propagates into async graph nodes scheduled in the same asyncio task
(LangGraph awaits node coroutines inline) — the same caveat as prompt_runtime applies to
thread/process hops.

resolve_skills_cached wraps skill_store.resolve_active_skills with a short TTL cache so
the turn loop can re-resolve every turn without hitting Postgres each time;
invalidate_skills_cache lets the skills-write path evict stale entries immediately.
"""
from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import AsyncIterator, Optional

from shared.services.skill_store import ResolvedSkill, resolve_active_skills

logger = logging.getLogger(__name__)

# {agent_id: {skill_key: ResolvedSkill}} — copy-on-write so concurrent tasks never
# mutate a shared dict (identical discipline to prompt_runtime's override var).
_skills_var: ContextVar[dict] = ContextVar("active_skills", default={})

_skill_cache: dict[tuple, tuple[float, list[ResolvedSkill]]] = {}


def _current_skills(agent_id: str) -> dict:
    return _skills_var.get().get(str(agent_id), {})


def get_skill_tools(agent_id: str) -> list:
    """[] when no skills are in context for this agent; else ONE load_skill tool.

    The tool closes over agent_id so a graph shared across agents still loads only its
    own skills. Rebuilt per call so the current contextvar snapshot is captured."""
    if not _current_skills(agent_id):
        return []

    from langchain_core.tools import tool

    aid = str(agent_id)

    @tool
    async def load_skill(name: str) -> str:
        """Load the full instructions for one available skill by its key.

        Call this BEFORE applying a skill listed in AVAILABLE SKILLS. `name` is the
        skill key (e.g. "story-splitting-spidr"). Returns the skill's full body."""
        skills = _current_skills(aid)
        hit = skills.get(str(name).strip())
        if hit is None:
            valid = ", ".join(sorted(skills.keys())) or "(none)"
            return (f"Unknown skill '{name}'. Available skill keys: {valid}. "
                    "Call load_skill with one of these exact keys.")
        header = f"# Skill: {hit.display_name} ({hit.skill_key})\n"
        return header + (hit.body or "")

    return [load_skill]


@asynccontextmanager
async def skill_context_scope(
    agent_id: str, skills: "list[ResolvedSkill]"
) -> AsyncIterator[None]:
    """Set the agent's loaded-skill map on enter (no-op when empty), clear on exit.

    Guarantees the skill channel never outlives the turn even if the wrapped invocation
    raises — the load_skill tool disappears again the moment the turn ends."""
    if not skills:
        yield
        return
    aid = str(agent_id)
    current = _skills_var.get()
    updated = dict(current)
    updated[aid] = {s.skill_key: s for s in skills}
    _skills_var.set(updated)
    try:
        yield
    finally:
        latest = _skills_var.get()
        if aid in latest:
            cleared = dict(latest)
            del cleared[aid]
            _skills_var.set(cleared)


async def resolve_skills_cached(
    tenant_id,
    agent_id,
    workspace_id=None,
    project_id=None,
    *,
    ttl: float = 45.0,
) -> list[ResolvedSkill]:
    """resolve_active_skills(...) with a short TTL cache to spare per-turn Postgres hits."""
    key = (str(tenant_id), str(agent_id), str(workspace_id), str(project_id))
    now = time.monotonic()
    cached = _skill_cache.get(key)
    if cached is not None and cached[0] > now:
        return cached[1]
    try:
        skills = await resolve_active_skills(tenant_id, agent_id, workspace_id, project_id)
    except Exception as exc:  # noqa: BLE001 — resolve_active_skills is already fail-soft
        logger.warning("resolve_skills_cached(%s/%s) failed: %s", tenant_id, agent_id, exc)
        skills = []
    _skill_cache[key] = (now + ttl, skills)
    return skills


def invalidate_skills_cache(tenant_id=None, agent_id=None) -> None:
    """Drop cache entries matching the filters; both None clears everything."""
    if tenant_id is None and agent_id is None:
        _skill_cache.clear()
        return
    for key in list(_skill_cache.keys()):
        cached_tenant, cached_agent, _, _ = key
        if tenant_id is not None and cached_tenant != str(tenant_id):
            continue
        if agent_id is not None and cached_agent != str(agent_id):
            continue
        _skill_cache.pop(key, None)
