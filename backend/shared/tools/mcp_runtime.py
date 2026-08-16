"""Per-invocation MCP tool injection for LangGraph agents.

Mirrors the connector contextvar pattern in config/connectors/context.py: the
A stage runner (or any caller) resolves the run's selected MCP servers into
LangChain tools and calls set_mcp_tools(...) before graph.ainvoke; the agent node
binds base_tools + get_mcp_tools() and the tools node dispatches over the same
combined set. clear_mcp_tools() runs in the caller's finally block so tools never
survive task completion.

Unlike connector context, get_mcp_tools() RETURNS [] when unset (MCP is optional —
absence means "no extra tools", not a misconfiguration).
"""
from __future__ import annotations

import contextvars
from typing import TYPE_CHECKING, Callable

from langgraph.prebuilt import ToolNode

if TYPE_CHECKING:
    from langchain_core.tools import BaseTool

_mcp_tools_var: contextvars.ContextVar[list] = contextvars.ContextVar(
    "active_mcp_tools", default=[]
)

# Shared, agent-agnostic prompt note. Append to any agent's system prompt so the
# model can tell its MCP-provided tools apart from native built-ins and answer
# "which MCP tools/servers do you have?" correctly. Relies on the `<server>__<tool>`
# namespacing applied to every MCP tool in shared/services/mcp_client._namespace.
MCP_TOOLS_PROMPT_NOTE = """

MCP TOOLS: some of your tools may be provided by externally-registered MCP (Model
Context Protocol) servers rather than native built-ins. Every MCP-provided tool is
namespaced as `<server>__<tool>` — the part before the double underscore is the MCP
server name. Native built-in tools never contain a double underscore. When the user
asks which MCP tools or MCP servers you have access to, identify them by this
`<server>__<tool>` convention and list exactly those (grouped by server). Never claim
you have no MCP tools when tools matching this pattern are present in your toolset.
"""


def set_mcp_tools(tools: "list[BaseTool]") -> None:
    _mcp_tools_var.set(list(tools or []))


def get_mcp_tools() -> "list[BaseTool]":
    return list(_mcp_tools_var.get() or [])


def clear_mcp_tools() -> None:
    _mcp_tools_var.set([])


def make_dynamic_tool_node(
    base_tools: "list[BaseTool]", agent_id: "str | None" = None
) -> Callable:
    """Return a LangGraph node that dispatches over base_tools + the contextvar MCP tools
    (and, when agent_id is given, the per-turn skill tools — i.e. load_skill).

    A fresh ToolNode is built per call so dynamically injected MCP/skill tools are
    resolvable without recompiling the graph. ToolNode handles tool lookup,
    parallel tool_calls, and error formatting — we only widen its tool set.
    agent_id defaults to None so existing callers stay unchanged (no skill tools).
    """

    async def _node(state):
        tools = list(base_tools)
        if agent_id is not None:
            from shared.services.skill_runtime import get_skill_tools
            tools = tools + get_skill_tools(agent_id)
        tools = tools + get_mcp_tools()
        return await ToolNode(tools).ainvoke(state)

    return _node
