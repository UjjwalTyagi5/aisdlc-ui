"""Security Agent — LangGraph graph for layered security scanning.

Follows the same StateGraph pattern as code_review_agent:
  agent node (LLM) -> tools node -> agent node (loop until done)

Import as: from agents_orchestrator.security_agent.agents.scanner import app
"""
from __future__ import annotations

import logging
import re
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
from shared.tools.document_approval import make_approval_tools
from shared.tools.mcp_runtime import get_mcp_tools, make_dynamic_tool_node, MCP_TOOLS_PROMPT_NOTE
from shared.services.skill_runtime import get_skill_tools
from shared.services.prompt_runtime import get_prompt_override


_logger = logging.getLogger(__name__)


class AgentState(TypedDict):
    messages: Annotated[Sequence[BaseMessage], add_messages]
    tenant_id: Optional[str]
    project_id: Optional[str]
    model_id: Optional[str]
    offering_id: Optional[str]
    # BYOK-resolved model carried through state so the tools node can re-establish it —
    # LangGraph may run nodes in separate task contexts where the contextvar the agent
    # node set does not reach a tool.
    resolved_model: Any



try:
    from shared.tools.project_documents import make_document_tools  # noqa: PLC0415

    # Bound to this agent's stage: it decides what "its own agent" means for an
    # approved-but-uncovered document, and it is what the evidence trail records as
    # the reader. Never a tool argument — a prompt could then claim another agent.
    _DOCUMENT_TOOLS = make_document_tools("security")
except Exception:  # noqa: BLE001 — a missing optional tool must not break the agent
    _DOCUMENT_TOOLS = []

try:
    from shared.tools.sharepoint_artifacts import make_sharepoint_tools  # noqa: PLC0415

    # Publishes APPROVED documents only, and cannot delete anything from the library —
    # see shared/tools/sharepoint_artifacts. `agent_id` and `stage` are bound here and
    # never taken from a tool argument, or a prompt could claim another agent's grant.
    _SHAREPOINT_TOOLS = make_sharepoint_tools(agent_id="security", stage="security")
except Exception:  # noqa: BLE001 — a missing optional tool must not break the agent
    _SHAREPOINT_TOOLS = []

try:
    from shared.tools.confluence_artifacts import make_confluence_tools  # noqa: PLC0415

    # THE SAME CAPABILITY, FOR THE OTHER DOCUMENT SYSTEM. Asked to publish an approved
    # document to Confluence, this agent used to answer that the platform "only
    # publishes to SharePoint" — truthfully, because the Confluence tools existed in
    # the connector and were bound to no agent but Documentation. Bound the same way as
    # SharePoint: agent and stage fixed here, never taken from a tool argument.
    _CONFLUENCE_TOOLS = make_confluence_tools(agent_id="security", stage="security")
except Exception:  # noqa: BLE001 — a missing optional tool must not break the agent
    _CONFLUENCE_TOOLS = []
    logger.warning("Security agent: Confluence document tools unavailable")

_tools = [
    scan_dependencies,
    scan_code,
    scan_secrets,
    generate_sbom,
    read_repo_file,
    search_repo,
    read_design_artifacts,
    submit_security_review,
    # The filed report is a DRAFT; the same raise the Documents panel's button does.
    *make_approval_tools("security"),
    *_DOCUMENT_TOOLS,
    *_SHAREPOINT_TOOLS,
    *_CONFLUENCE_TOOLS,
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
    """Resolve the tenant's BYOK model, then invoke it with messages + system prompt.

    THE RESOLUTION STEP IS LOAD-BEARING: `_resolve_model` below calls
    `resolve_chat_model`, which only READS a model the run already resolved (a
    contextvar). Nothing in this agent ever resolved one, so that read returned None
    every turn and the helper fell through to its local-dev `ANTHROPIC_API_KEY`
    fallback — dead on an Azure deployment, surfacing as
    "AnthropicException - API key is invalid" while a valid Azure key sat configured.
    Mirrors requirements_agent/agents/planning.py::agent and the same fix applied to
    the Code Review agent.
    """
    from langchain_core.messages import AIMessage, SystemMessage

    from shared.services.model_resolver import (
        ModelNotEnabledError,
        NoModelConfiguredError,
        resolve_model_for_run,
        set_resolved_model,
    )

    tenant_id = state.get("tenant_id", "") or ""
    try:
        resolved = await resolve_model_for_run(
            tenant_id,
            state.get("model_id"),
            offering_id=state.get("offering_id"),
            # Explicit, because this agent attaches its audit handler directly rather
            # than through shared/observability/callbacks.py — the only thing that
            # threads the run's project into the contextvar the resolver reads. A None
            # project filters EVERY offering out ("grants configured but none apply").
            project_id=state.get("project_id"),
        )
    except (NoModelConfiguredError, ModelNotEnabledError) as exc:
        _logger.warning(
            "Security model resolution failed (tenant=%s): %s", tenant_id, type(exc).__name__
        )
        return {"messages": [AIMessage(content=(
            "No usable model is configured for your organization. "
            "An administrator must add and verify a model provider in "
            "Org Settings → Model Providers."))]}
    except Exception:  # noqa: BLE001 — surfaced to the user, logged with the traceback
        _logger.exception("Security model resolution error (tenant=%s)", tenant_id)
        return {"messages": [AIMessage(content=(
            "The configured model could not be initialized. Please ask an administrator "
            "to re-verify the model provider in Org Settings → Model Providers."))]}
    set_resolved_model(resolved)

    model = _resolve_model(state)
    base = get_prompt_override("security") or SECURITY_SYSTEM_PROMPT
    messages = [SystemMessage(content=base + MCP_TOOLS_PROMPT_NOTE)] + list(state["messages"])
    response = await model.ainvoke(messages)
    return {"messages": [response], "resolved_model": resolved}


# The nudge text doubles as its own marker: counting it in the transcript is how
# `route_fn` knows it has already asked once, with no extra state to keep in sync.
_SUBMIT_NUDGE = (
    "You wrote the security review as prose instead of submitting it. The Security UI "
    "reads ONLY the submit_security_review artifact — a chat answer leaves the Summary "
    "and Findings tabs empty and records no PASS/FAIL verdict, so that scan effectively "
    "did not happen. Call submit_security_review now, once, with exactly the findings "
    "you just described, as the JSON object your instructions specify. Do not restate "
    "the review as text."
)


def _this_turn(state: AgentState) -> list:
    """Messages since the reader's last real message — this turn, not the session.

    The chat is one LangGraph thread with a MemorySaver, so scanning the whole transcript
    latched both checks below: one submission (or one nudge) anywhere in the session and
    every later scan in the same chat routed straight to END, prose and all. The nudge
    itself arrives as a HumanMessage and is excluded by text — it is ours."""
    msgs = state["messages"]
    start = 0
    for i, m in enumerate(msgs):
        content = getattr(m, "content", "") or ""
        if m.__class__.__name__ == "HumanMessage" and (not isinstance(content, str) or _SUBMIT_NUDGE not in content):
            start = i
    return msgs[start:]


#: The reader asking for a scan or review, in the words the page and people use. Not
#: "Send the security report for approval" — a request ABOUT a report is not a scan.
_SCAN_REQUEST_RE = re.compile(r"\b(?:re-?)?(?:scan|review)\b.{0,40}\b(?:the|this|that|my|it|branch|pr|code|repo)\b|\bsecurity (?:scan|review)\b", re.IGNORECASE)
_SCAN_TOOLS = {"scan_dependencies", "scan_code", "scan_secrets", "generate_sbom"}


def _is_scan_turn(state: AgentState) -> bool:
    """True when this turn is a scan: the reader asked for one, or the agent ran a scanner.

    THE NUDGE FIRED ON EVERY TURN THAT DID NOT SUBMIT — the Code Review agent, which
    shares this graph shape, was made to file a second review when asked only to send
    its report for approval. Same guard here, before it is observed."""
    turn = _this_turn(state)
    if turn and turn[0].__class__.__name__ == "HumanMessage":
        asked = turn[0].content if isinstance(turn[0].content, str) else ""
        if _SCAN_REQUEST_RE.search(asked):
            return True
    for m in turn:
        for tc in getattr(m, "tool_calls", None) or []:
            name = tc.get("name") if isinstance(tc, dict) else getattr(tc, "name", None)
            if name in _SCAN_TOOLS:
                return True
    return False


def _has_submitted(state: AgentState) -> bool:
    """True once submit_security_review has been called for THIS turn's scan."""
    for m in _this_turn(state):
        for tc in getattr(m, "tool_calls", None) or []:
            name = tc.get("name") if isinstance(tc, dict) else getattr(tc, "name", None)
            if name == "submit_security_review":
                return True
    return False


def _already_nudged(state: AgentState) -> bool:
    return any(
        _SUBMIT_NUDGE in (getattr(m, "content", "") or "")
        for m in _this_turn(state)
        if isinstance(getattr(m, "content", None), str)
    )


def route_fn(state: AgentState) -> str:
    """tools -> tools node; a prose 'review' with nothing submitted -> one nudge; else END.

    The persisted artifact is what the Summary/Findings tabs render and what carries
    the mandatory PASS/FAIL/CONDITIONAL verdict (PRD: a failing verdict blocks
    deployment). It exists only if the model calls submit_security_review. The Code
    Review agent was observed producing a complete review entirely as chat markdown,
    calling no tool, persisting nothing; this agent shares that graph shape, so it
    gets the same guard. Bounded to one retry by the nudge's presence in the
    transcript.
    """
    last = state["messages"][-1]
    if getattr(last, "tool_calls", None):
        return "tools"
    if _is_scan_turn(state) and not _has_submitted(state) and not _already_nudged(state):
        return "finalize"
    return END


async def finalize_node(state: AgentState) -> dict:
    """Ask once for the structured submission, then hand back to the agent."""
    from langchain_core.messages import HumanMessage

    _logger.info("Security: review produced without submit_security_review — nudging once")
    return {"messages": [HumanMessage(content=_SUBMIT_NUDGE)]}


_tool_node = make_dynamic_tool_node(_tools, agent_id="security")


async def tools_node(state: AgentState):
    """Dispatch tools, re-establishing the BYOK model in this node's context first."""
    try:
        from shared.services.model_resolver import get_resolved_model, set_resolved_model
        if get_resolved_model() is None and state.get("resolved_model") is not None:
            set_resolved_model(state["resolved_model"])
    except Exception:  # noqa: BLE001 — defensive; tools degrade with their own message
        pass
    return await _tool_node(state)


graph = StateGraph(AgentState)
graph.add_node("agent", agent_node)
graph.add_node("tools", tools_node)
graph.add_node("finalize", finalize_node)
graph.add_edge(START, "agent")
graph.add_conditional_edges(
    "agent", route_fn, {"tools": "tools", "finalize": "finalize", END: END}
)
graph.add_edge("tools", "agent")
graph.add_edge("finalize", "agent")
app = graph.compile(checkpointer=MemorySaver())
