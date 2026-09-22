"""Standalone Documentation agent — LangGraph agent+tools graph.

Mirrors the Deployment / Code Review / Security standalone graphs: an LLM agent node
that binds native doc tools + any BYO MCP tools (deduped by name), and a dynamic
tool node. Import as:
    from agents_orchestrator.documentation_agent.agents.compiler import app
"""
from __future__ import annotations

from typing import Annotated, Any, Optional, Sequence

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
    save_handover_document,
    save_kt_document,
    open_docs_pr,
    ingest_sharepoint_document,
    read_wiki_page,
    list_wiki_pages,
    diff_markdown_sections,
    save_runbook_update,
    save_knowledge_article,
    publish_to_confluence,
    list_confluence_pages,
    ingest_confluence_page,
)
from shared.tools.mcp_runtime import get_mcp_tools, make_dynamic_tool_node
from shared.tools.document_approval import make_approval_tools
from shared.services.skill_runtime import get_skill_tools
from shared.services.prompt_runtime import get_prompt_override


class AgentState(TypedDict):
    messages: Annotated[Sequence[BaseMessage], add_messages]
    tenant_id: Optional[str]
    project_id: Optional[str]
    model_id: Optional[str]
    offering_id: Optional[str]
    # The run's resolved model, carried so the tools node can re-establish it: LangGraph
    # may run a node in a task context the agent node's contextvar does not reach.
    resolved_model: Any



try:
    from shared.tools.project_documents import make_document_tools  # noqa: PLC0415

    # Bound to this agent's stage: it decides what "its own agent" means for an
    # approved-but-uncovered document, and it is what the evidence trail records as
    # the reader. Never a tool argument — a prompt could then claim another agent.
    _DOCUMENT_TOOLS = make_document_tools("documentation")
except Exception:  # noqa: BLE001 — a missing optional tool must not break the agent
    _DOCUMENT_TOOLS = []

try:
    from shared.tools.sharepoint_artifacts import make_sharepoint_tools  # noqa: PLC0415

    # THE APPROVED-ONLY PUBLISH, replacing this agent's own `publish_to_sharepoint`.
    # That one filed `session.generated_docs` — whatever had been written during this
    # conversation, held in memory and reviewed by nobody — straight into the business's
    # document library, where people who were not in the chat read it and where nothing
    # in this platform can delete it again. It was the only agent that could do that.
    # Now `save_document` records every deliverable as a PENDING artifact, so there is
    # an approval for publishing to wait on, exactly as on every other stage.
    _SHAREPOINT_TOOLS = make_sharepoint_tools(agent_id="documentation", stage="documentation")
except Exception:  # noqa: BLE001
    _SHAREPOINT_TOOLS = []

_tools = [
    inspect_repo,
    read_repo_file,
    search_repo,
    generate_changelog,
    read_upstream_artifacts,
    save_document,
    # The two onboarding deliverables. Separate tools rather than a doc_type on
    # save_document because each forces the sections that make it worth reading: a
    # handover with no "known risks" and a KT document with no setup steps are the
    # exact failures these replace, and a free-form save cannot refuse them.
    save_handover_document,
    save_kt_document,
    open_docs_pr,
    # SharePoint destination — additive to the local-disk save and the git-PR path.
    # Publishing is approved-only (see _SHAREPOINT_TOOLS); ingesting is read-only.
    ingest_sharepoint_document,
    read_wiki_page,
    list_wiki_pages,
    diff_markdown_sections,
    save_runbook_update,
    save_knowledge_article,
    # Confluence destination — same shape, additive alongside SharePoint.
    publish_to_confluence,
    list_confluence_pages,
    ingest_confluence_page,
    *_DOCUMENT_TOOLS,
    *_SHAREPOINT_TOOLS,
    # A saved document is a DRAFT; this is the same raise the Documents panel's button does.
    *make_approval_tools("documentation"),
]


def _resolve_model(state: AgentState):
    from config.env import ANTHROPIC_MODEL

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
    """Resolve the run's model, then invoke it.

    THE RESOLUTION STEP IS LOAD-BEARING. `resolve_chat_model` only READS a model the run
    already resolved (a contextvar); nothing in this agent resolved one, so every turn
    failed "No BYOK model resolved for this run" — whatever the page's picker said. The
    Security and Code Review agents had the same gap and the same fix. A resolution
    failure is raised, not answered as chat: the handler ends the turn as failed with
    the reason.
    """
    from langchain_core.messages import SystemMessage

    from shared.services.model_resolver import resolve_model_for_run, set_resolved_model

    resolved = await resolve_model_for_run(
        state.get("tenant_id") or "",
        state.get("model_id"),
        offering_id=state.get("offering_id"),
        # Explicit: a None project filters every offering out.
        project_id=state.get("project_id"),
    )
    set_resolved_model(resolved)
    model = _resolve_model(state)
    # Per-workspace agent-profile override (contextvar), falls back to the baked prompt.
    base = get_prompt_override("documentation") or DOC_SYSTEM_PROMPT
    messages = [SystemMessage(content=base)] + list(state["messages"])
    response = await model.ainvoke(messages)
    return {"messages": [response], "resolved_model": resolved}


_tool_node = make_dynamic_tool_node(_tools, agent_id="documentation")


async def tools_node(state: AgentState):
    """Dispatch tools with the run's model re-established in this node's context."""
    from shared.services.model_resolver import get_resolved_model, set_resolved_model

    if get_resolved_model() is None and state.get("resolved_model") is not None:
        set_resolved_model(state["resolved_model"])
    return await _tool_node(state)


def route_fn(state: AgentState) -> str:
    last = state["messages"][-1]
    if hasattr(last, "tool_calls") and last.tool_calls:
        return "tools"
    return END


graph = StateGraph(AgentState)
graph.add_node("agent", agent_node)
graph.add_node("tools", tools_node)
graph.add_edge(START, "agent")
graph.add_conditional_edges("agent", route_fn, {"tools": "tools", END: END})
graph.add_edge("tools", "agent")
app = graph.compile(checkpointer=MemorySaver())
