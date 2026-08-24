"""Standalone Documentation agent — LangGraph agent+tools graph.

Mirrors the Deployment / Code Review / Security standalone graphs: an LLM agent node
that binds native doc tools + any BYO MCP tools (deduped by name), and a dynamic
tool node. Import as:
    from agents_orchestrator.documentation_agent.agents.compiler import app
"""
from __future__ import annotations

from typing import Annotated, Optional, Sequence

from langchain_core.messages import BaseMessage
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.checkpoint.memory import MemorySaver
from typing_extensions import TypedDict

from agents_orchestrator.documentation_agent.prompts.doc_prompt import DOC_SYSTEM_PROMPT
from agents_orchestrator.documentation_agent.tools.doc_tools import (
    inspect_repo,
    read_repo_file,
    search_repo,
    generate_changelog,
    read_upstream_artifacts,
    save_document,
    open_docs_pr,
    publish_to_sharepoint,
    list_sharepoint_documents,
    ingest_sharepoint_document,
    publish_to_confluence,
    list_confluence_pages,
    ingest_confluence_page,
)
from shared.tools.mcp_runtime import get_mcp_tools, make_dynamic_tool_node
from shared.services.skill_runtime import get_skill_tools
from shared.services.prompt_runtime import get_prompt_override


class AgentState(TypedDict):
    messages: Annotated[Sequence[BaseMessage], add_messages]
    tenant_id: Optional[str]
    model_id: Optional[str]
    offering_id: Optional[str]


_tools = [
    inspect_repo,
    read_repo_file,
    search_repo,
    generate_changelog,
    read_upstream_artifacts,
    save_document,
    open_docs_pr,
    # SharePoint destination — additive to the local-disk save and the git-PR path.
    publish_to_sharepoint,
    list_sharepoint_documents,
    ingest_sharepoint_document,
    # Confluence destination — same shape, additive alongside SharePoint.
    publish_to_confluence,
    list_confluence_pages,
    ingest_confluence_page,
]


def _resolve_model(state: AgentState):
    from config.env import ANTHROPIC_API_KEY, ANTHROPIC_MODEL

    seen: set = set()
    tools = []
    for t in _tools + get_skill_tools("documentation") + get_mcp_tools():
        name = getattr(t, "name", None)
        if name in seen:
            continue
        seen.add(name)
        tools.append(t)

    # Per-workspace agent-profile override (contextvar), falls back to the baked prompt.
    base = get_prompt_override("documentation") or DOC_SYSTEM_PROMPT

    try:
        from shared.services.model_resolver import resolve_chat_model
        return resolve_chat_model(
            model_id=state.get("model_id") or ANTHROPIC_MODEL,
            offering_id=state.get("offering_id"),
            tools=tools,
            system_prompt=base,
        )
    except Exception:
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(
            model=state.get("model_id") or ANTHROPIC_MODEL,
            api_key=ANTHROPIC_API_KEY,
            max_tokens=8192,
        ).bind_tools(tools)


async def agent_node(state: AgentState) -> dict:
    from langchain_core.messages import SystemMessage

    model = _resolve_model(state)
    # Per-workspace agent-profile override (contextvar), falls back to the baked prompt.
    base = get_prompt_override("documentation") or DOC_SYSTEM_PROMPT
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
graph.add_node("tools", make_dynamic_tool_node(_tools, agent_id="documentation"))
graph.add_edge(START, "agent")
graph.add_conditional_edges("agent", route_fn, {"tools": "tools", END: END})
graph.add_edge("tools", "agent")
app = graph.compile(checkpointer=MemorySaver())
