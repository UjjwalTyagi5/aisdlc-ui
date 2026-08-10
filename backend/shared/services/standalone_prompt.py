"""Agent-profile prompt injection for the STANDALONE agent API surfaces.

Mirrors the copilot turn-loop wiring (copilot_api: resolve → inject_prompt →
prompt_override_scope / SystemMessage override) for each agent's OWN page/chat API.
The copilot already applies profiles on the orchestrated pipeline path; this makes
the same org→workspace→project prompt layers apply when a user talks to a single
agent directly.

One resolver, `resolve_injected_prompt`, covers both prompt shapes the copilot splits
into MESSAGE_PROMPT_STAGES vs SELF_INJECT_STAGES:

- Message-prompt agents (requirements/design/development) build the SystemMessage
  themselves. Call this with the (already-substituted) base and use the returned
  string as the SystemMessage content.
- Self-injecting agents (code_review/security/deployment/documentation) read the
  prompt back from prompt_runtime's contextvar (`get_prompt_override(agent_id) or
  BARE_CONSTANT`). Their handler resolves the injected prompt the same way over the
  BARE constant (no MCP note — the node re-appends its own suffix), then wraps the
  graph invocation in `prompt_override_scope(agent_id, injected)`.

Fail-soft everywhere: any profile miss/error returns the base prompt unchanged so a
turn is never broken. Empty tenant_id → no resolution possible → base returned.
"""
from __future__ import annotations

import logging
from typing import Optional

from shared.services.agent_profile_store import inject_prompt
from shared.services.prompt_runtime import (
    prepare_agent_turn,
    resolve_profile_cached,
    workspace_for_project,
)

logger = logging.getLogger(__name__)


async def resolve_injected_prompt(
    agent_id: str,
    base_prompt: Optional[str],
    tenant_id: Optional[str],
    project_id: Optional[str] = None,
) -> Optional[str]:
    """Compose the org/workspace/project agent profile over *base_prompt*.

    Returns the injected prompt, or *base_prompt* unchanged when no tenant is known or
    the profile resolve fails. The base is the FLOOR — profiles only add to it."""
    if not base_prompt or not tenant_id:
        return base_prompt
    try:
        workspace_id = await workspace_for_project(tenant_id, project_id)
        profile = await resolve_profile_cached(
            tenant_id, agent_id, workspace_id, project_id
        )
        return inject_prompt(base_prompt, profile)
    except Exception as exc:  # noqa: BLE001 — profile is an enhancement, never fatal
        logger.warning(
            "resolve_injected_prompt(%s) failed: %s — using base prompt", agent_id, exc
        )
        return base_prompt


async def resolve_agent_turn(
    agent_id: str,
    base_prompt: Optional[str],
    tenant_id: Optional[str],
    project_id: Optional[str] = None,
) -> tuple[Optional[str], list]:
    """Skills-aware successor to resolve_injected_prompt for the standalone surfaces.

    Returns ``(injected_prompt, skills)``: the profile+skills-index-composed prompt to
    use as the SystemMessage (message agents) or prompt_override (self-inject agents), and
    the ResolvedSkill list to hand to ``skill_context_scope`` around the graph invocation.
    Fail-soft to ``(base_prompt, [])`` on any miss/error — a turn is never broken."""
    injected, skills, _profile = await prepare_agent_turn(
        agent_id, base_prompt, tenant_id, project_id
    )
    return injected, skills


async def resolve_agent_skills(
    agent_id: str,
    tenant_id: Optional[str],
    project_id: Optional[str] = None,
) -> list:
    """Just the active ResolvedSkill list for this turn (no prompt work).

    For message-prompt agents (requirements/design/development) whose SystemMessage — and
    thus the skills index — is built only on first-turn init, but whose graph binds the
    load_skill tool from the contextvar on EVERY turn. Wrap the per-turn graph invocation
    in ``skill_context_scope(agent_id, await resolve_agent_skills(...))``. Fail-soft []."""
    if not tenant_id:
        return []
    try:
        from shared.services.skill_runtime import resolve_skills_cached

        workspace_id = await workspace_for_project(tenant_id, project_id)
        return await resolve_skills_cached(tenant_id, agent_id, workspace_id, project_id)
    except Exception as exc:  # noqa: BLE001 — skills are an enhancement, never fatal
        logger.warning("resolve_agent_skills(%s) failed: %s", agent_id, exc)
        return []
