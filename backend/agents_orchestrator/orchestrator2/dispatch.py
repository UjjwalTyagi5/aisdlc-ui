"""Dispatch one named agent and stream its output as protocol events.

Phase 2 dispatches only an explicitly named agent — there is no default agent
and no routing here (routing is Phase 3). An unknown or absent agent, or any
failure while the agent runs, yields a typed `error` event rather than raising
into the socket or failing silently: that silent-failure pattern is exactly
what let the old engine's Project Manager agent (`plan`) go undispatchable
without anyone noticing.

No gates, no sign-off, no positional progression: no `next_stage`, no
auto-advance, no stage-index arithmetic, and specifically no `HANDOFF::`
sentinel handling — that was the old engine's auto-advance mechanism and
there is no auto-advance in this engine.

Event type strings match the frontend contract in
`frontend/lib/orchestrator/protocol.ts` exactly: `agent.selected`,
`stream_chunk`, `tool.call`, `error`, `stream_end`. `run_agent` always yields
`stream_end` last, on every path including errors, and `agent.selected` is
always the first event yielded, before any text — the old engine switched
agents silently, so a wrong pick was invisible until the answer made no
sense.
"""
from __future__ import annotations

from typing import Any, AsyncIterator

from langchain_core.messages import HumanMessage, SystemMessage

from agents_orchestrator.orchestrator2.registry import get_capability, UnknownAgentError


async def run_agent(
    agent_id: str,
    *,
    text: str,
    run_id: str,
    tenant_id: str,
    model_id: str | None,
    offering_id: str | None,
) -> AsyncIterator[dict]:
    """Run `agent_id` on `text` and yield protocol events.

    Yields, in order: `agent.selected` first (unless the agent id is unknown,
    in which case an `error` event stands in for it), then zero or more
    `stream_chunk` / `tool.call` events as the graph runs, then `stream_end`
    always last. Any exception — resolving the capability, loading the graph
    or prompt, or running the graph — becomes an `error` event carrying the
    agent id; it never propagates to the caller and never yields nothing.
    """
    try:
        capability = get_capability(agent_id)
    except UnknownAgentError as exc:
        yield {"type": "error", "message": str(exc), "agent": agent_id}
        yield {"type": "stream_end"}
        return

    yield {"type": "agent.selected", "agent": agent_id, "reason": ""}

    try:
        graph = capability.load_graph()
        config = {"configurable": {"thread_id": run_id}, "recursion_limit": 100}

        if capability.mode == "stream":
            system_prompt = capability.load_prompt()
            state: dict[str, Any] = {
                "messages": [SystemMessage(content=system_prompt), HumanMessage(content=text)],
                "tenant_id": tenant_id,
                "model_id": model_id,
                "offering_id": offering_id,
            }
            async for chunk, _metadata in graph.astream(
                state, stream_mode="messages", config=config
            ):
                content = getattr(chunk, "content", None)
                if content:
                    yield {"type": "stream_chunk", "content": content}
        else:
            state = {
                "user_prompt": text,
                "tenant_id": tenant_id,
                "model_id": model_id,
                "offering_id": offering_id,
            }
            final_state = await graph.ainvoke(state, config=config)
            reply = (final_state or {}).get("final_user_message") or ""
            yield {"type": "stream_chunk", "content": reply}
    except Exception as exc:  # noqa: BLE001 - never let a run failure reach the socket unlabeled
        yield {"type": "error", "message": str(exc), "agent": agent_id}
    finally:
        yield {"type": "stream_end"}
