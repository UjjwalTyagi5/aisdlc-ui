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

BYOK MODEL RESOLUTION HAPPENS HERE, PER TURN, SCOPED TO THE RUN'S PROJECT
------------------------------------------------------------------------
This module previously threaded `model_id` / `offering_id` into graph state and
resolved NOTHING. Nothing set the `_RESOLVED_MODEL` contextvar that every agent's
`build_llm` / `resolve_chat_model` reads, so each agent either re-resolved on its
own (with no `project_id`, which is a different bug) or fell through to the
platform's `ANTHROPIC_API_KEY`. That is the exact failure that had the Testing
agent running on `local-env:anthropic` while an administrator looked at a
correctly-configured BYOK provider and wondered why it was never billed.

`resolve_model_for_run` is what enforces the project's grant
(`effective_project_offerings`) AND the project's monthly budget
(`check_budgets`). Both are keyed on `project_id`, and both are BYPASSED when it
is `None`. So the project id is not a nicety here — without it a project can use
a model it was never granted and spend past a cap its Business Unit set.

Two things are set before the graph runs, in this order:

  1. `set_run_project(project_id)` — the run-scoped contextvar, so budget checks
     made DEEPER in the stack (each agent's own `resolve_model_for_run`, which
     does not thread a project id) are project-scoped too.
  2. `set_resolved_model(resolved)` — the resolved model itself, which is what
     `build_llm` picks up inside every node and tool of the agent's graph.

FAILS CLOSED, LOUDLY, WITH NO ENV FALLBACK. `resolve_model_for_run` deliberately
does not fall back, and neither does this: a project with no usable model gets an
`error` event naming the problem and then `stream_end`, and the graph is never
invoked. A local fallback to the platform key would make this "work" on a laptop
and fail in a deployed environment where no such key exists — a lie about
production, and the reason this went unnoticed the first time.
"""
from __future__ import annotations

from typing import Any, AsyncIterator

from langchain_core.messages import HumanMessage, SystemMessage

from agents_orchestrator.orchestrator2.registry import get_capability, UnknownAgentError
from shared.services.model_resolver import (
    ModelNotEnabledError,
    NoModelConfiguredError,
    resolve_model_for_run,
    set_resolved_model,
    set_run_project,
)

# User-facing text for the two fail-closed resolver outcomes. Both name what an
# administrator has to do, because "model resolution failed" is not something the
# person reading it can act on. Neither ever carries a provider error string — a
# BYOK provider error can echo the tenant's own key back.
_NO_MODEL_MESSAGE = (
    "No usable model is configured for this project. An administrator must add and "
    "verify a model provider, and grant it to this project, in Org Settings → Model "
    "Providers."
)
_MODEL_NOT_ENABLED_MESSAGE = (
    "The model this run selected is not available to this project. An administrator "
    "must grant it to the project in Org Settings → Model Providers, or the run must "
    "select a model this project may use."
)


async def run_agent(
    agent_id: str,
    *,
    text: str,
    run_id: str,
    tenant_id: str,
    model_id: str | None,
    offering_id: str | None,
    project_id: str | None,
) -> AsyncIterator[dict]:
    """Run `agent_id` on `text` and yield protocol events.

    Yields, in order: `agent.selected` first (unless the agent id is unknown,
    in which case an `error` event stands in for it), then zero or more
    `stream_chunk` / `tool.call` events as the graph runs, then `stream_end`
    always last. Any exception — resolving the capability, resolving the BYOK
    model, loading the graph or prompt, or running the graph — becomes an
    `error` event; it never propagates to the caller and never yields nothing.
    The `error` event's `agent` field carries the resolved agent id when one
    resolved (a failure while running a known agent), and is OMITTED when the
    id itself is unknown — `OrchestratorAgentId` in the frontend contract is
    an enum of the nine valid ids, so an unresolved id in that field would
    fail Zod validation and the frame would be silently dropped, exactly the
    failure mode this phase exists to eliminate.

    `project_id` is KEYWORD-REQUIRED WITH NO DEFAULT, on purpose. It is the
    scope that decides which models this run may use and whose budget it spends
    against (see the module docstring). A default of `None` would let a new
    call site drop project scoping silently, which is the shape of the bug this
    function was changed to fix; a caller with genuinely no project must say so
    by passing `None` explicitly, and will then fail closed on any tenant that
    has grants configured. It comes from the `runs` row — never from the client
    (see `ws._resolve_run`).
    """
    try:
        capability = get_capability(agent_id)
    except UnknownAgentError as exc:
        # `agent` is deliberately OMITTED here: the frontend contract
        # (frontend/lib/orchestrator/protocol.ts) types ErrorEvent.agent as
        # OrchestratorAgentId.optional() — an enum of the nine valid ids, not a
        # plain string. `agent_id` here is the id that FAILED to resolve (e.g.
        # "nope"), so including it would make Zod's safeParse reject the frame
        # and the client would drop it — the one event whose entire job is to
        # surface "that agent does not exist" would be the one guaranteed not
        # to arrive. The unresolved id is still visible in `message`
        # (str(exc) names it), so no information is lost.
        yield {"type": "error", "message": str(exc)}
        yield {"type": "stream_end"}
        return

    yield {"type": "agent.selected", "agent": agent_id, "reason": "", "run_id": run_id}

    try:
        # ── BYOK, project-scoped ─────────────────────────────────────────────
        # Order matters and is asserted by the tests. `set_run_project` FIRST so
        # that anything re-resolving deeper in the graph (each agent's own
        # `resolve_model_for_run`, which passes no project id) still gets the
        # project's grant and the project's budget via the contextvar fallback.
        # Then resolve, then stash — all BEFORE the graph is loaded or invoked,
        # because `build_llm` inside the very first node reads the stashed model.
        set_run_project(project_id)
        try:
            resolved = await resolve_model_for_run(
                tenant_id,
                model_id,
                offering_id=offering_id,
                project_id=project_id,
            )
        except (NoModelConfiguredError, ModelNotEnabledError) as exc:
            # FAIL CLOSED. No env key, no platform key, no "org default" retry
            # without the project. Clear any model a previous turn on this socket
            # stashed so a later code path cannot pick up another project's
            # resolution, then say what is wrong and end the turn.
            set_resolved_model(None)
            yield {
                "type": "error",
                "message": (
                    _MODEL_NOT_ENABLED_MESSAGE
                    if isinstance(exc, ModelNotEnabledError)
                    else _NO_MODEL_MESSAGE
                ),
                "detail": str(exc),
                "agent": agent_id,
            }
            return
        set_resolved_model(resolved)

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
        # Unlike the UnknownAgentError branch above, `agent_id` HAS already
        # resolved by this point (agent.selected was yielded with it), so it
        # is one of the nine valid ids and is safe to include against the
        # OrchestratorAgentId enum in ErrorEvent.agent.
        yield {"type": "error", "message": str(exc), "agent": agent_id}
    finally:
        yield {"type": "stream_end"}
