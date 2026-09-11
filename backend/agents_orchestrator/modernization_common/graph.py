"""The agent-and-tools graph both Track 3 agents run on.

Same two-node shape as every Portfolio 1 agent (`requirements_agent/agents/planning.py`,
`pm_agent/agents/schedule.py`): an `agent` node that calls the model with the tools
bound, and a `tools` node that runs whatever it asked for, looping until the model
answers in plain text. Written once here rather than copied twice, because the parts
that are easy to get wrong are the same for both agents:

  · BYOK, PROJECT-SCOPED. The Orchestrator's dispatch resolves the run's model before
    the graph starts and leaves it on the `_RESOLVED_MODEL` contextvar; the node uses
    that when present. A standalone turn has nothing set, so the node resolves it —
    with the PROJECT id, because `effective_project_offerings` fails closed without one
    once a tenant has any model grant. No env-key fallback on any path.
  · TOOL-CALL PAIRING. A turn stopped between the model's tool call and the tool's
    answer leaves an unanswered call in the checkpoint, and every later turn on that
    thread is rejected by the provider. `sanitize_tool_call_pairing` repairs it.
  · BOUNDED TOOL OUTPUT. An uncapped tool result (a whole dependency list) re-enters
    every later call and silently exhausts the context window.
  · MCP AND SKILL TOOLS are bound per turn from their contextvars, never baked into
    the compiled graph, so one project's tools cannot leak into another's turn.
"""
from __future__ import annotations

import logging
from typing import Annotated, Any, Sequence

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict

logger = logging.getLogger(__name__)

#: Characters of one tool result allowed back into the model's context.
TOOL_OUTPUT_CAP = 12_000


class ToolAgentState(TypedDict, total=False):
    messages: Annotated[list[BaseMessage], add_messages]
    tenant_id: str
    model_id: str | None
    offering_id: str | None
    project_id: str | None


def cap_tool_output(content: Any, cap: int = TOOL_OUTPUT_CAP) -> Any:
    """Truncate a tool result to `cap` characters, saying so in the text."""
    if not isinstance(content, str) or len(content) <= cap:
        return content
    dropped = len(content) - cap
    return (
        content[:cap]
        + f"\n\n…[output truncated — dropped {dropped:,} characters to protect the context window]"
    )


_CLIENTS: dict[tuple, Any] = {}


def _chat_client(resolved: Any, max_tokens: int) -> Any:
    """A streaming ChatLiteLLM for the resolved BYOK model, cached per credential.

    The credential fingerprint is part of the key: the alias alone survives a key
    rotation and kept handing back a client built with the old secret.
    """
    from shared.services.model_resolver import (  # noqa: PLC0415
        credential_fingerprint,
        litellm_key_kwargs,
        temperature_kwargs,
    )

    key = (
        resolved.alias, resolved.model, credential_fingerprint(resolved.api_key, resolved.base_url),
        resolved.base_url or "", max_tokens,
    )
    if key not in _CLIENTS:
        from langchain_litellm import ChatLiteLLM  # noqa: PLC0415 — ~7s import, deferred

        _CLIENTS[key] = ChatLiteLLM(
            model=resolved.model,
            custom_llm_provider=resolved.litellm_provider,
            api_base=resolved.base_url,
            api_key=resolved.api_key,
            **litellm_key_kwargs(resolved.litellm_provider, resolved.api_key),
            **temperature_kwargs(resolved.model, 0.2),
            max_tokens=max_tokens,
            # guarded_completion is the only retry loop; ChatLiteLLM's own would
            # multiply every call against a provider that is already rate limiting.
            max_retries=0,
            streaming=True,
        )
    return _CLIENTS[key]


def build_tool_agent_graph(
    *,
    agent_type: str,
    tools: Sequence[Any],
    checkpoint_name: str,
    max_tokens: int = 8096,
):
    """Compile the agent+tools graph for `agent_type` over `tools`."""
    from config.checkpoint import build_checkpointer  # noqa: PLC0415
    from shared.tools.mcp_runtime import get_mcp_tools, make_dynamic_tool_node  # noqa: PLC0415

    base_tools = list(tools)
    run_tools = make_dynamic_tool_node(base_tools, agent_id=agent_type)

    async def agent(state: ToolAgentState) -> dict:
        from config.ws_helper import get_project_id  # noqa: PLC0415
        from shared.services.message_pairing import sanitize_tool_call_pairing  # noqa: PLC0415
        from shared.services.model_call_wrapper import guarded_completion  # noqa: PLC0415
        from shared.services.model_errors import friendly_model_error  # noqa: PLC0415
        from shared.services.model_resolver import (  # noqa: PLC0415
            ModelNotEnabledError,
            NoModelConfiguredError,
            get_resolved_model,
            resolve_model_for_run,
            set_resolved_model,
        )
        from shared.services.skill_runtime import get_skill_tools  # noqa: PLC0415

        tenant_id = state.get("tenant_id") or ""
        resolved = get_resolved_model()
        if resolved is None:
            try:
                resolved = await resolve_model_for_run(
                    tenant_id,
                    state.get("model_id"),
                    offering_id=state.get("offering_id"),
                    project_id=state.get("project_id") or get_project_id(),
                )
            except (NoModelConfiguredError, ModelNotEnabledError) as exc:
                logger.warning("%s: model resolution failed (tenant=%s): %s",
                               agent_type, tenant_id, type(exc).__name__)
                return {"messages": [AIMessage(content=(
                    "No usable model is configured for this project. An administrator must add "
                    "and verify a model provider, and grant it to this project, in Org Settings "
                    "→ Model Providers."
                ))]}
            set_resolved_model(resolved)

        try:
            client = _chat_client(resolved, max_tokens).bind_tools(
                base_tools + get_skill_tools(agent_type) + get_mcp_tools()
            )
            response = await guarded_completion(
                resolved, client, sanitize_tool_call_pairing(list(state["messages"])),
                tenant_id=tenant_id, agent_type=agent_type,
                config={"metadata": {"user_api_key_alias": resolved.alias}},
            )
            return {"messages": [response]}
        except Exception as exc:  # noqa: BLE001 — a model failure is a reply, not a crash
            logger.exception("%s agent call failed (tenant=%s)", agent_type, tenant_id)
            return {"messages": [AIMessage(content=friendly_model_error(exc))]}

    async def tools_node(state: ToolAgentState) -> dict:
        result = await run_tools(state)
        messages = result.get("messages", []) if isinstance(result, dict) else result
        capped: list[Any] = []
        for message in messages:
            if isinstance(message, ToolMessage):
                message.content = cap_tool_output(message.content)
            capped.append(message)
        return {"messages": capped}

    def should_continue(state: ToolAgentState) -> str:
        last = state["messages"][-1]
        return "tools" if getattr(last, "tool_calls", None) else END

    workflow = StateGraph(ToolAgentState)
    workflow.add_node("agent", agent)
    workflow.add_node("tools", tools_node)
    workflow.set_entry_point("agent")
    workflow.add_conditional_edges("agent", should_continue, {"tools": "tools", END: END})
    workflow.add_edge("tools", "agent")
    return workflow.compile(checkpointer=build_checkpointer(checkpoint_name))
