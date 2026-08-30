"""Security Agent — LangGraph graph for layered security scanning.

Follows the same StateGraph pattern as code_review_agent:
  agent node (LLM) -> tools node -> agent node (loop until done)

Import as: from agents_orchestrator.security_agent.agents.scanner import app
"""
from __future__ import annotations

from typing import Annotated, Any, Optional, Sequence

from langchain_core.messages import BaseMessage
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.checkpoint.memory import MemorySaver
from typing_extensions import TypedDict

from agents_orchestrator.security_agent.prompts.security_prompt import SECURITY_SYSTEM_PROMPT
from agents_orchestrator.security_agent.tools.security_tools import (
    scan_dependencies,
    scan_code,
    scan_secrets,
    generate_sbom,
    read_repo_file,
    search_repo,
    read_design_artifacts,
    submit_security_review,
)
from shared.tools.mcp_runtime import get_mcp_tools, make_dynamic_tool_node, MCP_TOOLS_PROMPT_NOTE
from shared.services.skill_runtime import get_skill_tools
from shared.services.prompt_runtime import get_prompt_override


class AgentState(TypedDict):
    messages: Annotated[Sequence[BaseMessage], add_messages]
    tenant_id: Optional[str]
    model_id: Optional[str]
    offering_id: Optional[str]


_tools = [
    scan_dependencies,
    scan_code,
    scan_secrets,
    generate_sbom,
    read_repo_file,
    search_repo,
    read_design_artifacts,
    submit_security_review,
]


def _resolve_model(state: AgentState):
    """Resolve the LLM model for this invocation from the run's BYOK-resolved model.

    Fails CLOSED in enterprise: a tenant with no configured provider gets an error,
    never the platform's key. Mirrors code_review_agent/agents/reviewer.py exactly."""
    from config.env import ANTHROPIC_MODEL

    model_id = state.get("model_id") or ANTHROPIC_MODEL
    offering_id = state.get("offering_id")

    # Dedup by tool name (native wins) — the model API rejects duplicate names,
    # which happens when two BYO MCP servers expose a like-named tool.
    seen: set = set()
    tools = []
    for t in _tools + get_skill_tools("security") + get_mcp_tools():
        name = getattr(t, "name", None)
        if name in seen:
            continue
        seen.add(name)
        tools.append(t)

    # Per-workspace agent-profile override (contextvar), falls back to the baked prompt.
    base = get_prompt_override("security") or SECURITY_SYSTEM_PROMPT

    # resolve_chat_model fails CLOSED in enterprise and falls back to ANTHROPIC_API_KEY
    # only in local dev. Do NOT wrap this in a bare `except Exception` — doing so used
    # to swallow the ImportError from a resolver symbol that did not exist, so every
    # run silently billed the PLATFORM key and skipped budgets, grants and rate limits.
    from shared.services.model_resolver import resolve_chat_model

    return resolve_chat_model(
        model_id=model_id,
        offering_id=offering_id,
        tools=tools,
        system_prompt=base,
    )


async def agent_node(state: AgentState) -> dict:
    """Invoke the LLM with the current messages + system prompt."""
    from langchain_core.messages import SystemMessage

    model = _resolve_model(state)
    base = get_prompt_override("security") or SECURITY_SYSTEM_PROMPT
    messages = [SystemMessage(content=base + MCP_TOOLS_PROMPT_NOTE)] + list(state["messages"])
    response = await model.ainvoke(messages)
    return {"messages": [response]}


def route_fn(state: AgentState) -> str:
    """Route to tools if the last message has tool calls, otherwise end."""
    last = state["messages"][-1]
    if hasattr(last, "tool_calls") and last.tool_calls:
        return "tools"
    return END


graph = StateGraph(AgentState)
graph.add_node("agent", agent_node)
graph.add_node("tools", make_dynamic_tool_node(_tools, agent_id="security"))
graph.add_edge(START, "agent")
graph.add_conditional_edges("agent", route_fn, {"tools": "tools", END: END})
graph.add_edge("tools", "agent")
app = graph.compile(checkpointer=MemorySaver())
