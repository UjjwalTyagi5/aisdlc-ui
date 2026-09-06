"""Code Review Agent — LangGraph graph for reviewing code diffs.

Follows the same StateGraph pattern as the requirements and design agents:
  agent node (LLM) -> tools node -> agent node (loop until done)

The graph is compiled with MemorySaver (local dev) or PostgresSaver (enterprise).
Import as: from agents_orchestrator.code_review_agent.agents.reviewer import app
"""
from __future__ import annotations

import logging
from typing import Annotated, Any, Optional, Sequence

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


_logger = logging.getLogger(__name__)


class AgentState(TypedDict):
    messages: Annotated[Sequence[BaseMessage], add_messages]
    tenant_id: Optional[str]
    project_id: Optional[str]
    model_id: Optional[str]
    offering_id: Optional[str]
    # BYOK-resolved model carried through state so the tools node can re-establish it.
    # LangGraph may run nodes in separate task contexts, where the contextvar the agent
    # node set does not reliably reach a tool — same reason design's AgentState carries
    # it (design_architecture_agent/agents/architecture.py).
    resolved_model: Any


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
    from config.env import ANTHROPIC_MODEL

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

    THE RESOLUTION STEP IS LOAD-BEARING, and its absence is what made this agent
    unusable: `_resolve_model` below calls `resolve_chat_model`, which only READS the
    model a run already resolved (a contextvar). Nothing in this agent ever resolved
    one, so that read returned None on every turn and the helper fell through to its
    local-dev `ANTHROPIC_API_KEY` fallback — - which is dead on this deployment (the
    tenant's provider is Azure), surfacing to the user as
    "AnthropicException - API key is invalid" while an entirely valid Azure key sat
    configured. Every working agent resolves here first; this one simply never did.
    Mirrors requirements_agent/agents/planning.py::agent.
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
            project_id=state.get("project_id"),
        )
    except (NoModelConfiguredError, ModelNotEnabledError) as exc:
        _logger.warning(
            "Code Review model resolution failed (tenant=%s): %s", tenant_id, type(exc).__name__
        )
        return {"messages": [AIMessage(content=(
            "No usable model is configured for your organization. "
            "An administrator must add and verify a model provider in "
            "Org Settings → Model Providers."))]}
    except Exception:  # noqa: BLE001 — surfaced to the user, logged with the traceback
        _logger.exception("Code Review model resolution error (tenant=%s)", tenant_id)
        return {"messages": [AIMessage(content=(
            "The configured model could not be initialized. Please ask an administrator "
            "to re-verify the model provider in Org Settings → Model Providers."))]}
    set_resolved_model(resolved)

    model = _resolve_model(state)
    # ON THE NUDGE TURN, REQUIRE THE CALL RATHER THAN REQUESTING IT. Asking in prose
    # ("call submit_code_review now") is still just prose to a model that has already
    # ignored the same instruction in the system prompt: observed live on
    # azure/gpt-5-mini, which answered the nudge with a SECOND markdown review and left
    # the tabs empty again. `tool_choice` moves it from persuasion to protocol — the
    # provider will not return a plain message for this turn. Verified against this
    # deployment's own azure/gpt-5-mini before relying on it.
    #
    # Only on that turn: forcing it generally would stop the agent reading files or
    # running semgrep first, and the nudge only fires once the model has already
    # written the finished review it is now being made to submit.
    if _is_nudge_turn(state):
        model = model.bind(
            tool_choice={"type": "function", "function": {"name": "submit_code_review"}}
        )
    # Per-workspace agent-profile override (contextvar), falls back to the baked prompt.
    base = get_prompt_override("code_review") or CODE_REVIEW_SYSTEM_PROMPT
    messages = [SystemMessage(content=base + MCP_TOOLS_PROMPT_NOTE)] + list(state["messages"])
    response = await model.ainvoke(messages)
    return {"messages": [response], "resolved_model": resolved}


# The nudge text is also its own marker: counting it in the transcript is how
# `route_fn` knows it has already asked once, without extra state to keep in sync.
_SUBMIT_NUDGE = (
    "You wrote the review as prose instead of submitting it. The reviewer UI reads ONLY "
    "the submit_code_review artifact — a chat answer leaves the Summary and Findings tabs "
    "empty, so that review effectively did not happen. Call submit_code_review now, once, "
    "with exactly the findings you just described, as the JSON object your instructions "
    "specify. Do not restate the review as text."
)


def _this_turn(state: AgentState) -> list:
    """Messages since the reader's last real message — this turn, not the session.

    The chat session is one LangGraph thread with a MemorySaver, so the transcript
    keeps growing across turns. Scanning all of it made both checks below latch
    permanently: one review submitted (or one nudge spent) anywhere in the session and
    every LATER review in that same chat would route straight to END, prose and all.
    The reader asking for a second review is a new turn and gets the same guarantee as
    the first. The nudge itself arrives as a HumanMessage, so it is excluded by text —
    it is ours, not the reader's.
    """
    msgs = state["messages"]
    start = 0
    for i, m in enumerate(msgs):
        content = getattr(m, "content", "") or ""
        is_human = m.__class__.__name__ == "HumanMessage"
        if is_human and (not isinstance(content, str) or _SUBMIT_NUDGE not in content):
            start = i
    return msgs[start:]


def _has_submitted(state: AgentState) -> bool:
    """True once submit_code_review has been called for THIS turn's review."""
    for m in _this_turn(state):
        for tc in getattr(m, "tool_calls", None) or []:
            name = tc.get("name") if isinstance(tc, dict) else getattr(tc, "name", None)
            if name == "submit_code_review":
                return True
    return False


def _already_nudged(state: AgentState) -> bool:
    return any(
        _SUBMIT_NUDGE in (getattr(m, "content", "") or "")
        for m in _this_turn(state)
        if isinstance(getattr(m, "content", None), str)
    )


def _is_nudge_turn(state: AgentState) -> bool:
    """True when the message the agent is about to answer IS the nudge."""
    msgs = state["messages"]
    if not msgs:
        return False
    content = getattr(msgs[-1], "content", "") or ""
    return isinstance(content, str) and _SUBMIT_NUDGE in content


def route_fn(state: AgentState) -> str:
    """tools -> tools node; a prose 'review' with nothing submitted -> one nudge; else END.

    WHY THE NUDGE EXISTS: the persisted artifact is what the Summary/Findings tabs
    render, and it only exists if the model calls submit_code_review. Observed live on
    azure/gpt-5-mini: the agent produced a complete, accurate review — summary, three
    findings with file/line, recommendations, autofix patches, a merge recommendation —
    entirely as chat markdown, called no tool, and the run persisted nothing. The
    system prompt already says "Finish by calling submit_code_review"; a prompt line is
    not an enforcement mechanism. Asking once, in-graph, converts the model's own
    just-written review into the structured call instead of losing it.

    Bounded to a single retry by the nudge's presence in the transcript, so a model
    that simply will not call the tool ends the turn rather than looping.
    """
    last = state["messages"][-1]
    if getattr(last, "tool_calls", None):
        return "tools"
    if not _has_submitted(state) and not _already_nudged(state):
        return "finalize"
    return END


async def finalize_node(state: AgentState) -> dict:
    """Ask once for the structured submission, then hand back to the agent."""
    from langchain_core.messages import HumanMessage

    _logger.info("Code Review: review produced without submit_code_review — nudging once")
    return {"messages": [HumanMessage(content=_SUBMIT_NUDGE)]}


_tool_node = make_dynamic_tool_node(_tools, agent_id="code_review")


async def tools_node(state: AgentState):
    """Dispatch tools, re-establishing the BYOK model in this node's context first.

    LangGraph may run the tools node in a separate task context, where the contextvar
    `agent_node` set did not propagate — a tool that resolves a model would then fail
    "No model resolved". Read it back from state (no second DB round trip), the same
    way the design agent's `action` node does.
    """
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
