"""Code Review Agent — LangGraph graph for reviewing code diffs.

Follows the same StateGraph pattern as the requirements and design agents:
  agent node (LLM) -> tools node -> agent node (loop until done)

The graph is compiled with MemorySaver (local dev) or PostgresSaver (enterprise).
Import as: from agents_orchestrator.code_review_agent.agents.reviewer import app
"""
from __future__ import annotations

from typing import Annotated, Optional, Sequence

from langchain_core.messages import BaseMessage
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.checkpoint.memory import MemorySaver
from typing_extensions import TypedDict

from agents_orchestrator.code_review_agent.prompts.review_prompt import CODE_REVIEW_SYSTEM_PROMPT
from agents_orchestrator.code_review_agent.tools.semgrep_tool import run_semgrep_scan
from agents_orchestrator.code_review_agent.tools.diff_tool import analyze_diff
from agents_orchestrator.code_review_agent.tools.review_tools import (
    read_repo_file,
    search_repo,
    read_requirements_payload,
    read_design_artifacts,
    submit_code_review,
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
    run_semgrep_scan,
    analyze_diff,
    read_repo_file,
    search_repo,
    read_requirements_payload,
    read_design_artifacts,
    submit_code_review,
]


def _resolve_model(state: AgentState):
    """Resolve the LLM model for this invocation."""
    from config.env import ANTHROPIC_API_KEY, ANTHROPIC_MODEL

    model_id = state.get("model_id") or ANTHROPIC_MODEL
    offering_id = state.get("offering_id")

    # Base tools + any MCP tools injected for this stage/run (mcp_runtime contextvar).
    # Dedup by tool name (native wins) — the model API rejects duplicate tool names,
    # which happens when two BYO MCP servers expose a like-named tool.
    seen: set[str] = set()
    tools = []
    for t in _tools + get_skill_tools("code_review") + get_mcp_tools():
        name = getattr(t, "name", None)
        if name in seen:
            continue
        seen.add(name)
        tools.append(t)

    # Per-workspace agent-profile override (contextvar), falls back to the baked prompt.
    base = get_prompt_override("code_review") or CODE_REVIEW_SYSTEM_PROMPT

    try:
        from shared.services.model_resolver import resolve_chat_model
        return resolve_chat_model(
            model_id=model_id,
            offering_id=offering_id,
            tools=tools,
            system_prompt=base,
        )
    except Exception:
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(
            model=ANTHROPIC_MODEL,
            api_key=ANTHROPIC_API_KEY,
            max_tokens=8192,
        ).bind_tools(tools)


async def agent_node(state: AgentState) -> dict:
    """Invoke the LLM with the current messages + system prompt."""
    from langchain_core.messages import SystemMessage

    model = _resolve_model(state)
    # Per-workspace agent-profile override (contextvar), falls back to the baked prompt.
    base = get_prompt_override("code_review") or CODE_REVIEW_SYSTEM_PROMPT
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
graph.add_node("tools", make_dynamic_tool_node(_tools, agent_id="code_review"))
graph.add_edge(START, "agent")
graph.add_conditional_edges("agent", route_fn, {"tools": "tools", END: END})
graph.add_edge("tools", "agent")
app = graph.compile(checkpointer=MemorySaver())
