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
    if injected:
        injected = await with_approved_documents(agent_id, injected, tenant_id, project_id)
    return injected, skills


DOCUMENTS_UNAVAILABLE_NOTE = (
    "--- APPROVED DOCUMENTS IN THIS PROJECT ---\n"
    "The project's approved documents could not be listed for this turn. They may "
    "still exist: call `list_project_documents` before telling the user there are none."
    "\n--- END APPROVED DOCUMENTS IN THIS PROJECT ---"
)


async def with_approved_documents(
    agent_id: str, prompt: str, tenant_id: Optional[str], project_id: Optional[str]
) -> str:
    """`prompt` plus the project's approved-documents block, for an agent that can read
    them.

    THE STANDALONE AGENTS WERE BUILT BESIDE THE DOCUMENT RECORD, NOT ON IT. Asked "does
    this project have any approved artifacts?" from inside the project's own page, the
    Development agent answered "no approved artifacts in this session" — its tools were
    bound but nothing told it the record existed, and (until `bind_turn_project`) the
    tools could not see the project anyway. The Orchestrator gained this block first
    (`orchestrator2.project_documents`); this puts the same block, same wording, into
    every standalone agent that binds `list_project_documents`/`read_document`, so
    what an agent knows about the project's record does not depend on which door the
    user came through.

    Metadata only — names, ids, approvers — never contents; the agent reads what it
    needs with `read_document`. Agents without the tools get nothing added: a block
    that says "call read_document" to an agent that cannot is a trap. A failed read
    is said, not hidden as "no documents".
    """
    if not (prompt and tenant_id and project_id):
        return prompt
    from shared.tools.project_documents import has_document_tools  # noqa: PLC0415

    if not has_document_tools(agent_id):
        return prompt
    try:
        from agents_orchestrator.orchestrator2.project_documents import (  # noqa: PLC0415
            approved_documents_context,
        )

        block = await approved_documents_context(str(project_id), str(tenant_id))
    except Exception:  # noqa: BLE001 — an outage is said, never read as "none"
        logger.warning("approved documents block unavailable for %s", agent_id, exc_info=True)
        block = DOCUMENTS_UNAVAILABLE_NOTE
    if not block:
        return prompt
    return f"{prompt.rstrip()}\n\n{block.strip()}"


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
