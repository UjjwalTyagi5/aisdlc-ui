"""Standalone Deployment agent — LangGraph agent+tools graph.

Mirrors the Code Review / Security standalone graphs: an LLM agent node that binds
native deploy tools + any BYO MCP tools (deduped by name), and a dynamic tool node.
Import as: from agents_orchestrator.deployment_agent.agents.deployer import app
"""
from __future__ import annotations

import logging

from typing import Annotated, Optional, Sequence

from langchain_core.messages import BaseMessage
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.checkpoint.memory import MemorySaver
from typing_extensions import TypedDict

from agents_orchestrator.deployment_agent.prompts.deploy_prompt import DEPLOY_SYSTEM_PROMPT
from agents_orchestrator.deployment_agent.tools.deploy_tools import (
    inspect_repo,
    read_repo_file,
    search_repo,
    read_upstream_artifacts,
    sync_repo,
    find_infrastructure_code,
    read_infrastructure_plan,
    plan_deploy_package,
    plan_security_scans,
    read_quality_gate,
    stage_deploy_file,
    open_deploy_pr,
    submit_release,
)
from agents_orchestrator.deployment_agent.tools.pipeline_tools import (
    check_deployment_request,
    get_pipeline_runs,
    get_run_status,
    list_pipelines,
    list_service_connections,
    request_pipeline_creation,
    request_pipeline_run,
)
from shared.tools.mcp_runtime import get_mcp_tools, make_dynamic_tool_node
from shared.services.skill_runtime import get_skill_tools
from shared.services.prompt_runtime import get_prompt_override


logger = logging.getLogger(__name__)


class AgentState(TypedDict):
    messages: Annotated[Sequence[BaseMessage], add_messages]
    tenant_id: Optional[str]
    model_id: Optional[str]
    offering_id: Optional[str]


_tools = [
    inspect_repo,
    read_repo_file,
    search_repo,
    read_upstream_artifacts,
    sync_repo,
    find_infrastructure_code,
    read_infrastructure_plan,
    plan_deploy_package,
    plan_security_scans,
    read_quality_gate,
    stage_deploy_file,
    open_deploy_pr,
    submit_release,
    # Pipeline lifecycle. The two request_* tools file an approval and perform
    # nothing; see tools/pipeline_tools.py.
    list_pipelines,
    list_service_connections,
    get_pipeline_runs,
    get_run_status,
    request_pipeline_creation,
    request_pipeline_run,
    check_deployment_request,
]


def _resolve_model(state: AgentState):
    from config.env import ANTHROPIC_MODEL

    seen: set = set()
    tools = []
    for t in _tools + get_skill_tools("deployment") + get_mcp_tools():
        name = getattr(t, "name", None)
        if name in seen:
            continue
        seen.add(name)
        tools.append(t)

    # Per-workspace agent-profile override (contextvar), falls back to the baked prompt.
    base = get_prompt_override("deployment") or DEPLOY_SYSTEM_PROMPT

    # resolve_chat_model fails CLOSED in enterprise and falls back to ANTHROPIC_API_KEY
    # only in local dev. Do NOT wrap this in a bare `except Exception` — doing so used
    # to swallow the ImportError from a resolver symbol that did not exist, so every
    # run silently billed the PLATFORM key and skipped budgets, grants and rate limits.
    from shared.services.model_resolver import resolve_chat_model

    return resolve_chat_model(
        model_id=state.get("model_id") or ANTHROPIC_MODEL,
        offering_id=state.get("offering_id"),
        tools=tools,
        system_prompt=base,
    )


async def agent_node(state: AgentState) -> dict:
    from langchain_core.messages import AIMessage, SystemMessage
    from shared.services.model_resolver import (  # noqa: PLC0415
        ModelNotEnabledError, NoModelConfiguredError, get_resolved_model,
        resolve_model_for_run, set_resolved_model,
    )

    # THE RUN HAS TO RESOLVE ITS OWN MODEL. `resolve_chat_model` below only READS a
    # model the run already stashed — it resolves nothing. The orchestrated path fills
    # that stash in shared/routers/runs.py, but the standalone chat route never did, so
    # every chat turn fell through to the fallback and, without ANTHROPIC_API_KEY or in
    # enterprise mode, raised NoModelConfiguredError. That error blames the
    # administrator for a model provider that is configured perfectly well, which is
    # why it survived: the message points away from the bug.
    if get_resolved_model() is None:
        tenant_id = state.get("tenant_id") or ""
        try:
            resolved = await resolve_model_for_run(
                tenant_id, state.get("model_id"), offering_id=state.get("offering_id")
            )
        except (NoModelConfiguredError, ModelNotEnabledError) as exc:
            logger.warning(
                "deployment model resolution failed (tenant=%s): %s",
                tenant_id, type(exc).__name__,
            )
            return {"messages": [AIMessage(content=(
                "No usable model is configured for your organization. An administrator "
                "must add and verify a model provider in Org Settings -> Model "
                "Providers."))]}
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "deployment model resolution error (tenant=%s): %s",
                tenant_id, type(exc).__name__,
            )
            from shared.services.model_errors import friendly_model_error  # noqa: PLC0415

            return {"messages": [AIMessage(content=friendly_model_error(exc))]}
        set_resolved_model(resolved)

    model = _resolve_model(state)
    # Per-workspace agent-profile override (contextvar), falls back to the baked prompt.
    base = get_prompt_override("deployment") or DEPLOY_SYSTEM_PROMPT
    messages = [SystemMessage(content=base)] + list(state["messages"])
    response = await model.ainvoke(messages)
    return {"messages": [response]}


def route_fn(state: AgentState) -> str:
    last = state["messages"][-1]
    if hasattr(last, "tool_calls") and last.tool_calls:
        return "tools"
    return END


graph = StateGraph(AgentState)
graph.add_node("agent", agent_node)
graph.add_node("tools", make_dynamic_tool_node(_tools, agent_id="deployment"))
graph.add_edge(START, "agent")
graph.add_conditional_edges("agent", route_fn, {"tools": "tools", END: END})
graph.add_edge("tools", "agent")
app = graph.compile(checkpointer=MemorySaver())
