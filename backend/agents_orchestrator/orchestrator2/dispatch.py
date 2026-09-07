"""Dispatch one named agent and stream its output as protocol events.

This module dispatches the agent it is TOLD to dispatch and chooses nothing: the
decision is made in `router.route` and applied by `ws.py`, which passes the chosen
id and the `reason` the user is shown. Keeping the choice out of here is deliberate —
one place decides, one place runs, and `agent.selected` cannot disagree with what
actually ran. An unknown or absent agent, or any
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
`stream_end` last on every path it is allowed to finish — including every error
path — and `agent.selected` is always the first event yielded, before any text:
the old engine switched agents silently, so a wrong pick was invisible until the
answer made no sense. (A consumer that closes or abandons the generator early
ends the turn itself and does not see `stream_end`; there is no consumer left to
receive it.)

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
  2. `set_resolved_model(resolved)` — the resolved model, which is what an agent's
     `build_llm` / `resolve_chat_model` reads when it builds its client.

THE CLEAR THAT CARRIES THE GUARANTEE IS THE ONE ON ENTRY, NOT THE ONE ON EXIT.
`run_agent` clears both contextvars as its FIRST statement, before anything can
fail, so a turn never reads the previous turn's project or key no matter how the
previous turn ended. That clear runs in the caller's own context — an async
generator's body executes in the context of whoever is driving it — so on a socket
that serves many turns it lands exactly where it has to.

The `finally` clears them again, but that is defence in depth and nothing more.
This is an async generator, and a caller that ABANDONS one — stops iterating
without closing it, which is what `ws.py` does when `_send` raises an
`EventSerializationError` mid-stream and then deliberately keeps serving the
socket — does not run the `finally` inline. CPython's asyncgen finalizer runs
`aclose()` in a NEW TASK, whose context is a COPY, so `set_resolved_model(None)`
made there lands on that copy and never reaches the socket. `ws.py` therefore
closes the generator explicitly (`contextlib.aclosing`), which makes its exit-clear
land inline; a future call site that forgets to is covered by the ENTRY clear, not
by the `finally`.

What is NOT claimed: that the contextvars are empty at every instant between
turns. After an abandoned turn the previous value can sit on the socket's context
until the next turn starts. Nothing reads it there — between turns the socket only
receives a frame, validates it and resolves the run, none of which builds an LLM
client — and the next turn's entry-clear precedes every read.

FAILS CLOSED, LOUDLY, WITH NO ENV FALLBACK. `resolve_model_for_run` deliberately
does not fall back, and neither does this: a project with no usable model gets an
`error` event naming the problem and then `stream_end`, and the graph is never
invoked. A local fallback to the platform key would make this "work" on a laptop
and fail in a deployed environment where no such key exists — a lie about
production, and the reason this went unnoticed the first time.

WHAT THIS LAYER GUARANTEES, AND WHERE THE GUARANTEE STOPS
---------------------------------------------------------
Precisely this much: the run's model is resolved against the run's project, and
both contextvars are set, BEFORE the graph is loaded or invoked — and if it cannot
be resolved, no graph runs at all and the user is told why. Nothing here reads an
environment key on any path.

It does NOT follow that a project's own key is always the one used end to end.
`build_llm` reads a CONTEXTVAR, and a contextvar reaches only as far as the context
does. An agent that crosses an executor boundary without carrying it — `asyncio.run`
inside a node, `run_in_executor` without `copy_context()` — finds nothing set and
takes ITS OWN fallback, which this module cannot prevent.

The known instance is the Testing agent:
`agents_orchestrator/testing_agent/config/shared.py:154-176` falls back to
`ANTHROPIC_API_KEY` whenever the contextvar is missing, `AGENT_RUNTIME_MODE !=
"enterprise"` — the default is `"local"` (`config/env.py:155`) — AND that env key is
non-empty; an empty key raises there instead, so the fallback is a local-dev
convenience, not a silent production path. That agent's own runner only keeps the
contextvar because it copies the context explicitly before `run_in_executor`
(`agents_orchestrator/testing_agent/agents/testing_agent.py:429-439`); this module
does not go through that runner, it awaits the compiled graph directly. The other
eight agents' executor boundaries are unaudited and are tracked as separate work —
deliberately not touched here.

So: this module closes the gap where NOTHING was resolved. Whether each agent then
holds on to what was resolved is that agent's property, not this one's, and the
end-to-end claim is only as strong as the weakest of the nine.
"""
from __future__ import annotations

import logging
from typing import Any, AsyncIterator, Mapping

from langchain_core.messages import HumanMessage, SystemMessage

from agents_orchestrator.orchestrator2 import (
    connectors,
    deliverables,
    dev_artifacts,
    mcp,
)
from config.ws_helper import (
    reset_session_id,
    set_consequential_approved,
    set_orchestrator_run,
    set_provider_kind,
    set_session_id,
    set_tenant_id,
    set_user_id,
)
from agents_orchestrator.orchestrator2.registry import get_capability, UnknownAgentError
from shared.authz.consequential import is_approval_message
from shared.services.model_resolver import (
    ModelNotEnabledError,
    NoModelConfiguredError,
    resolve_model_for_run,
    set_resolved_model,
    set_run_project,
)

logger = logging.getLogger(__name__)

# User-facing text for the two fail-closed resolver outcomes. Both name what an
# administrator has to do, because "model resolution failed" is not something the
# person reading it can act on. Neither ever carries a provider error string — a
# BYOK provider error can echo the tenant's own key back.
_NO_MODEL_MESSAGE = (
    "No usable model is configured for this project. An administrator must add and "
    "verify a model provider, and grant it to this project, in Org Settings → Model "
    "Providers."
)
_EMPTY_REPLY_MESSAGE = (
    "The agent finished without producing a reply. Nothing was saved. Try again, or "
    "rephrase what you asked for."
)
_MODEL_NOT_ENABLED_MESSAGE = (
    "The model this run selected is not available to this project. An administrator "
    "must grant it to the project in Org Settings → Model Providers, or the run must "
    "select a model this project may use."
)


def _clear_run_model_context() -> None:
    """Drop this context's resolved model and run project.

    One helper rather than two calls at each of the two call sites (entry and
    `finally`), so "cleared" means the same thing at both and a future third
    contextvar has one place to be added to.
    """
    set_resolved_model(None)
    set_run_project(None)


# The placeholder for a tool a provider did not name. `ToolCallEvent.name` in
# `frontend/lib/orchestrator/protocol.ts` is `z.string()` with NO default, so a frame
# whose name is null or empty fails `safeParse` and is dropped in the browser without a
# trace — the one behaviour this engine exists to remove. Announcing an unnamed tool as
# "a tool" is worse information than its real name and better than a frame that cannot
# arrive.
_UNNAMED_TOOL = "tool"


def _stream_text(content: Any) -> str:
    """The displayable text of one streamed chunk.

    `content` is a plain string for some providers and a LIST OF TYPED BLOCKS for
    others — Anthropic streams `[{"type": "text", "text": ...}]`. The list shape used
    to be forwarded verbatim, which `json.dumps` accepts, so nothing failed on the way
    out; it then failed `StreamChunkEvent.content: z.string()` in the browser and the
    frame was DROPPED. An agent that produced only block-list chunks appeared to say
    nothing at all.

    DELIBERATELY NOT `router._content_text`, which does the same traversal and then
    STRIPS. Stripping is correct there — whitespace changes no routing decision — and
    ruinous here: chunk boundaries fall inside sentences, so "Hello " + "world" would
    arrive as "Helloworld". Two functions, because the two callers genuinely want
    different things; `test_streamed_text_keeps_the_whitespace_that_joins_tokens`
    exists to stop a future tidy-up merging them.

    RAISES on a shape that is neither a string nor a list. The first version of this
    function returned `""` there, reasoning that one un-renderable fragment should not
    cost the whole turn — which was rationalising: it turned an unknown provider shape
    into an agent that silently says less than it said, with nothing in the transcript
    and nothing in the logs. That is the failure class this engine exists to remove,
    and it would have been a REGRESSION in visibility: the previous code forwarded the
    value to `json.dumps`, which raises on an object it cannot encode, so the socket
    reported a dropped frame. `test_the_socket_clears_the_turns_key_when_a_frame_cannot_
    be_encoded` caught exactly that and is why this raises instead.

    `run_agent`'s handler turns it into a typed `error` naming the agent, after the
    text that already streamed — visible, and consistent with `_history_messages`,
    which raises on an unreadable history shape for the same reason.

    An empty string, an empty list, and a list holding no text blocks are all
    UNDERSTOOD shapes that carry no text, and yield `""`. Only an unrecognised
    container raises.
    """
    if content is None:
        # An ABSENCE of text, not an unreadable shape — the canonical content of a
        # chunk that carried only tool calls, and of a message with no content at all.
        # `_history_messages` draws the same line for the same reason.
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, Mapping) and block.get("type") == "text":
                parts.append(str(block.get("text") or ""))
        return "".join(parts)
    raise TypeError(
        f"a streamed chunk carried content of type {type(content).__name__}, which is "
        f"neither a string nor a list of blocks"
    )


def _is_tool_result(message: Any) -> bool:
    """Whether this streamed message is a tool RESULT rather than the agent's prose.

    `tool_call_id` is the discriminator, not `.type`: a `ToolMessage` reports type
    `"tool"` but a `ToolMessageChunk` reports `"ToolMessageChunk"`, and streaming
    yields whichever the provider produces. Both carry `tool_call_id`; no AI or human
    message does.
    """
    return getattr(message, "tool_call_id", None) is not None


def _call_field(call: Any, field: str) -> Any:
    """One field of a tool-call chunk, whether it is a mapping or an object.

    LangChain normalises tool-call chunks to `TypedDict`s, but a provider integration
    may hand back an object. Reading both keeps a named tool from being announced as
    the unnamed placeholder purely because of its container's shape. (The same
    accommodation, for the same reason, as `router._call_field`.)
    """
    if isinstance(call, Mapping):
        return call.get(field)
    return getattr(call, field, None)


def _tool_name(value: Any) -> str:
    name = value if isinstance(value, str) else None
    return name.strip() if name and name.strip() else _UNNAMED_TOOL


async def run_agent(
    agent_id: str,
    *,
    text: str,
    run_id: str,
    tenant_id: str,
    model_id: str | None,
    offering_id: str | None,
    project_id: str | None,
    user_id: str,
    context: str,
    reason: str,
) -> AsyncIterator[dict]:
    """Run `agent_id` on `text` and yield protocol events.

    Yields, in order: `agent.selected` first (unless the agent id is unknown,
    in which case an `error` event stands in for it), then zero or more
    `stream_chunk` / `tool.call` events as the graph runs, then `stream_end`
    last on every path this generator is ALLOWED TO FINISH — including every
    error path. Any exception — resolving the capability, resolving the BYOK
    model, loading the graph or prompt, or running the graph — becomes an
    `error` event rather than propagating to the caller, so a consumer that
    iterates to the end always receives at least one event, and `stream_end`
    is always the last of them.

    WHAT IS NOT CLAIMED: that `stream_end` arrives no matter what. A consumer
    that CLOSES or ABANDONS this generator early ends the turn itself —
    `GeneratorExit` is raised at whichever `yield` was suspended and unwinds
    straight past the `stream_end` at the bottom, which is never reached. That
    is not a gap (there is no consumer left to receive it), but it is why this
    paragraph is qualified and the module docstring above is qualified the same
    way: an unnarrowed "always last" here would have this function's own
    docstring contradicting its module's, and a comment asserting a guarantee
    the code does not provide is worse than no comment at all.
    The `error` event's `agent` field carries the resolved agent id when one
    resolved (a failure while running a known agent), and is OMITTED when the
    id itself is unknown — `OrchestratorAgentId` in the frontend contract is
    an enum of the nine valid ids, so an unresolved id in that field would
    fail Zod validation and the frame would be silently dropped, exactly the
    failure mode this phase exists to eliminate.

    `context` is what the run already holds (`context.handoff_context`), or `""`.
    KEYWORD-REQUIRED WITH NO DEFAULT for the same reason as `project_id`: a default
    of `""` would let a call site drop the hand-off silently, and an agent that is
    handed nothing behaves exactly like an agent on a run where nothing has happened
    yet — it re-asks the user for work the run already contains. That is the defect
    the old engine's `_upstream_context` had, and it must not be reachable by
    forgetting an argument.

    `reason` is why THIS agent is answering, shown to the user in `agent.selected`.
    Also keyword-required: the event exists so that a wrong routing decision is
    visible and correctable in one turn, and an empty reason is an event that has
    stopped doing its job while still appearing to.

    `project_id` is KEYWORD-REQUIRED WITH NO DEFAULT, on purpose. It is the
    scope that decides which models this run may use and whose budget it spends
    against (see the module docstring). A default of `None` would let a new
    call site drop project scoping silently, which is the shape of the bug this
    function was changed to fix; a caller with genuinely no project must say so
    by passing `None` explicitly, and will then fail closed on any tenant that
    has grants configured. It comes from the `runs` row — never from the client
    (see `ws._resolve_run`).
    """
    # THIS IS THE LOAD-BEARING CLEAR. A socket serves many turns in one async
    # context and both contextvars are set per turn, so a turn that ends before
    # setting them would otherwise run — or be observed — under the PREVIOUS turn's
    # project and model. The unknown-agent branch below returns before either is
    # set and is the concrete case; clearing here rather than in that branch means
    # a future early return cannot reintroduce the same hole.
    #
    # It is load-bearing rather than belt-and-braces because it is the only clear
    # that is guaranteed to run in the CALLER'S context. A generator body executes
    # in the context of whoever drives it, and this statement runs on the first
    # `__anext__`, so it always lands on the socket. The `finally` at the bottom
    # does NOT, if the caller abandons this generator instead of closing it: see
    # the comment there.
    _clear_run_model_context()

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

    # The ONE `agent.selected` for this turn. `reason` is the caller's — the router's
    # explanation, or "you named it" for an explicit override — because this is the
    # event the user reads to see WHY this agent is answering, and a wrong routing
    # decision is only correctable if it is visible. Emitting an empty reason here and
    # a real one at the call site would put two of these on the wire, the second
    # erasing the first.
    yield {"type": "agent.selected", "agent": agent_id, "reason": reason,
           "run_id": run_id}

    # What the USER saw, accumulated from the events this generator yields rather than
    # from the graph's state — so a deliverable is exactly the reply that was
    # displayed, never a different rendering of it.
    reply_parts: list[str] = []
    # Only a turn that ran to completion is captured. See the capture block below.
    turn_completed = False
    # Bound BEFORE the try for the same reason `agent_id` is: the `finally` resets it,
    # and a failure before it was assigned would raise NameError from inside the
    # cleanup — the failure channel losing the failure.
    _session_token = None

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
            # without the project — just say what is wrong and end the turn. No
            # `return` here, and none anywhere else inside this `try`: `stream_end`
            # is yielded AFTER the `finally` (see below), and a `return` would run
            # the `finally` and then leave without it. The `else` below is what
            # keeps the success path off this branch.
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
        else:
            set_resolved_model(resolved)

            graph = capability.load_graph()
            config = {"configurable": {"thread_id": run_id}, "recursion_limit": 100}

            # The project's connector, bound for this turn only.
            #
            # The agents' board and repo tools do not take credentials as arguments;
            # they read a connector off `config.connectors.context`. Nothing here
            # bound one, so the Development agent on a project with Azure DevOps
            # connected reported that no ADO credentials were configured — truthfully,
            # because it could not see them. See orchestrator2/connectors.py for why
            # every argument below is load-bearing, `owner_id` especially: the
            # credential is usually a project-scoped PERSONAL one, and resolving it
            # without the turn's user yields a connector with no PAT, which the agent
            # reports exactly as it reports having no connector at all.
            # ── the agent's own per-run context ──────────────────────────────
            #
            # The agents' tools do not take a working directory or a user. They build
            # both from contextvars in `config/ws_helper`, which the standalone
            # `*_agent_api.py` wrappers set and this engine — skipping those wrappers
            # by D10a — did not.
            #
            # The cost was visible: the Development agent cloned the repo, said so, and
            # the Deliverables tab showed "No files yet", because the clone was keyed on
            # `get_session_id()` returning None while `runs.py::_run_dev_work_dir` looked
            # under the RUN id. Its docstring says "session_id == run_id"; that was true
            # of the wrapper and had to be made true here.
            #
            # SESSION ID IS THE RUN ID, matching the deliverables and the LangGraph
            # thread, so one conversation is one id everywhere. The user is the turn's
            # own, because the work_dir path is `files/<user>/orchestrator/<run>/project`.
            _session_token = set_session_id(run_id)
            set_user_id(user_id)
            # THE TENANT, alongside the user. `shared.authz.consequential.owner_approved`
            # reads both and refuses with "this run has none — it is running in the
            # background" if either is missing, so without this every Orchestrator turn
            # looked unattended and no consequential action could ever be authorised.
            # Reported: Requirements refused to create Azure Board work items for a
            # Project Admin who had just confirmed them.
            set_tenant_id(tenant_id)
            # This turn is the Orchestrator's, and `ws.py` has already verified the
            # caller administers the run's project. Per §1.5 that person owns every
            # stage here, which the stage-owner permissions do not otherwise say —
            # `project_admin` holds approval for `documentation` and `plan` alone.
            set_orchestrator_run(True)
            # DID THE USER APPROVE, on this turn? `authorize_consequential`'s second
            # half reads this, and nothing in orchestrator2 ever set it — so a user
            # who typed "yes" was asked again, and again. Reported: "the system
            # continues to block the write operation despite your multiple approvals",
            # while the same agent through its standalone wrapper wrote to the board
            # fine. That wrapper calls this; this engine skips wrappers.
            #
            # Set UNCONDITIONALLY from the current message, never only when it is an
            # approval: consent is for one action, and a yes that persisted would
            # authorise every later turn in the conversation.
            #
            # `is_approval_message` is the shared helper the standalone path uses. Two
            # notions of "yes" is how one surface accepts what the other refuses.
            set_consequential_approved(is_approval_message(text))
            # Which board/repo provider the agent's tools are talking to. Without it
            # they default to azure_devops, which is right today and silently wrong the
            # first time a project selects GitHub.
            try:
                set_provider_kind(
                    await connectors.connector_kind_for(
                        agent_id, tenant_id=tenant_id, project_id=str(project_id or "")
                    )
                )
            except Exception:  # noqa: BLE001 - a refinement, never fatal
                pass

            # The project's connector AND its MCP servers, both bound for this turn
            # only. The old engine wraps every turn in exactly these two; this engine
            # had neither. The connector gap announced itself — the Development agent
            # said it could not reach Azure DevOps — while the MCP one never would
            # have: the agent node binds whatever `mcp_runtime` holds, so an unbound
            # contextvar is an agent quietly missing every tool the project
            # registered, with no error and an answer that looks complete.
            async with connectors.bound_connector(
                agent_id,
                tenant_id=tenant_id,
                # From the verified `runs` row, like every other project-scoped
                # value on this path. Never from the client frame.
                project_id=project_id,
                owner_id=user_id,
            ), mcp.bound_mcp_tools(
                agent_id,
                tenant_id=tenant_id,
                project_id=project_id,
                owner_id=user_id,
            ):

                if capability.mode == "stream":
                    system_prompt = capability.load_prompt()
                    # The run's existing artifacts go in as a SECOND system message
                    # rather than being spliced into the agent's own prompt or prepended
                    # to the user's words. Each agent's prompt is a long, carefully
                    # written document (25,844 characters for Requirements), and editing
                    # one at runtime to carry data is how a prompt stops being reviewable;
                    # putting it in the human turn would have the agent answering a
                    # question the user did not ask.
                    messages: list[Any] = [SystemMessage(content=system_prompt)]
                    if context:
                        messages.append(SystemMessage(content=context))
                    messages.append(HumanMessage(content=text))
                    state: dict[str, Any] = {
                        "messages": messages,
                        "tenant_id": tenant_id,
                        "model_id": model_id,
                        "offering_id": offering_id,
                    }
                    # One `running` per TOOL, not per chunk: providers split a single tool
                    # call across many chunks that share an id, and only the first carries
                    # the name. Scoped to this turn, so the same tool used twice in one
                    # conversation is announced twice.
                    announced: set[str] = set()
                    async for chunk, _metadata in graph.astream(
                        state, stream_mode="messages", config=config
                    ):
                        if _is_tool_result(chunk):
                            # A tool RESULT. Its `content` is the tool's output, and
                            # forwarding it as a `stream_chunk` put it in the transcript as
                            # if the agent had said it — so it is reported as activity and
                            # its text is not streamed.
                            yield {
                                "type": "tool.call",
                                "name": _tool_name(getattr(chunk, "name", None)),
                                "status": "done",
                                "run_id": run_id,
                            }
                            continue

                        for call in getattr(chunk, "tool_call_chunks", None) or ():
                            key = str(_call_field(call, "id") or "")
                            name = _call_field(call, "name")
                            # A continuation chunk carries the id but no name. Waiting for
                            # a named one keeps the placeholder for tools that are never
                            # named at all, rather than spending it on the second half of
                            # a tool whose name already arrived.
                            if not name or key in announced:
                                continue
                            announced.add(key)
                            yield {
                                "type": "tool.call",
                                "name": _tool_name(name),
                                "status": "running",
                                "run_id": run_id,
                            }

                        text = _stream_text(getattr(chunk, "content", None))
                        if text:
                            reply_parts.append(text)
                            yield {"type": "stream_chunk", "content": text}
                else:
                    # Invoke-mode agents take a single prompt string and no message
                    # list, so the context is prepended to it. Same information, the only
                    # shape this graph accepts.
                    state = {
                        "user_prompt": f"{context}\n\n{text}" if context else text,
                        "tenant_id": tenant_id,
                        "model_id": model_id,
                        "offering_id": offering_id,
                    }
                    final_state = await graph.ainvoke(state, config=config)
                    reply = (final_state or {}).get("final_user_message") or ""
                    if reply:
                        reply_parts.append(reply)
                        yield {"type": "stream_chunk", "content": reply}
                    else:
                        # AN EMPTY REPLY IS A FAILED TURN, NOT A QUIET ONE.
                        #
                        # This used to yield the empty string regardless. The client skips
                        # a chunk with no content and then REMOVES the bubble that never
                        # received a token, so the user saw their own message, a line
                        # saying the agent was answering, and then nothing at all — no
                        # reply, no error, composer handed back. That is the "agent appears
                        # to say nothing" failure this engine was rebuilt to remove,
                        # reproduced one layer up.
                        #
                        # Reported here rather than papered over downstream: only this
                        # layer knows the graph finished and produced no message, and an
                        # invoke-mode graph that returns no `final_user_message` has not
                        # done its job.
                        yield {
                            "type": "error",
                            "message": _EMPTY_REPLY_MESSAGE,
                            "agent": agent_id,
                        }

        # Reached only when the graph ran to the end without raising — the connector
        # is unbound by then, so nothing below can reach a credential. Everything the
        # capture block does is gated on this.
        turn_completed = True
    except Exception as exc:  # noqa: BLE001 - never let a run failure reach the socket unlabeled
        # Unlike the UnknownAgentError branch above, `agent_id` HAS already
        # resolved by this point (agent.selected was yielded with it), so it
        # is one of the nine valid ids and is safe to include against the
        # OrchestratorAgentId enum in ErrorEvent.agent.
        yield {"type": "error", "message": str(exc), "agent": agent_id}
    finally:
        # DEFENCE IN DEPTH — the guarantee lives on the entry-clear at the top.
        #
        # It still earns its place on the inline paths. The first version cleared
        # the model only in the `NoModelConfiguredError` / `ModelNotEnabledError`
        # branch, which left the failure modes that raise THROUGH the generic
        # handler uncovered — `check_budgets` (BudgetExceededError /
        # BudgetWindowClosedError) and the per-model rate/cost enforcers all raise
        # from inside `resolve_model_for_run` AFTER a previous turn's model was
        # stashed. A budget-exceeded turn therefore ended with the PREVIOUS
        # project's ResolvedModel — and so its BYOK key — still live on this
        # socket's context.
        #
        # WHERE IT DOES NOT REACH: abandonment. If the consumer stops iterating
        # without closing this generator, the `finally` does not run inline —
        # CPython's asyncgen finalizer calls `aclose()` in a NEW TASK, whose
        # context is a copy, so the clear lands on the copy and the socket's
        # context keeps the value. `ws.py` avoids that by closing this generator
        # explicitly (`contextlib.aclosing`), which runs this block inline in the
        # socket's own context on the `_send`-failed path too; the entry-clear is
        # what covers any consumer that does not.
        #
        # Nothing may `yield` in here. On close, `GeneratorExit` is raised at
        # whichever `yield` was suspended and unwinds through this block; a `yield`
        # here would turn that into `RuntimeError: async generator ignored
        # GeneratorExit` — which is why `stream_end` is yielded BELOW this block
        # rather than inside it. On a normal exit that is the same last event as
        # before; on a close there is no consumer left to receive it.
        #
        # Safe to clear on the inline paths: the graph has finished by then (the
        # astream loop is exhausted, or ainvoke has returned, or an exception
        # unwound past it), and any task the graph spawned copied the context when
        # it was created, so this cannot retract a value a still-running node is
        # using.
        _clear_run_model_context()
        # The agent context names a run and a person. Left set, the next turn on this
        # socket — possibly another run — would clone into the previous one's directory.
        if _session_token is not None:
            try:
                reset_session_id(_session_token)
            except Exception:  # noqa: BLE001 - nothing useful remains to do
                pass
            set_user_id("")
            set_tenant_id(None)
            # CLEARED, always. A leaked True would carry the Orchestrator's
            # stage-ownership exemption into whatever ran next on this worker,
            # including a standalone agent's request, where the stage owner really
            # does decide.
            set_orchestrator_run(False)
            set_consequential_approved(False)

    # ── deliverable capture ──────────────────────────────────────────────────
    #
    # AFTER the `finally` and BEFORE `stream_end`, both deliberately.
    #
    # Not inside `finally`: nothing may yield there. On close, `GeneratorExit` is
    # raised at the suspended yield and unwinds through that block, and a yield inside
    # it becomes `RuntimeError: async generator ignored GeneratorExit` — which is the
    # same reason `stream_end` itself sits out here.
    #
    # Not in `ws.py` after its `async for` either: `stream_end` is yielded from HERE,
    # so capturing outside this generator would land `deliverable.ready` after the
    # client had already been told the turn was over — the panel would fill in after
    # the composer came back, which reads as a document arriving from nowhere.
    #
    # Only on a turn that actually completed. Capturing a partial reply from a failed
    # turn would file a truncated document under the agent's heading, where nothing
    # distinguishes it from a complete one.
    if turn_completed:
        # The clone and the PR, onto the run row. `pointers_for_run` builds the
        # "Repository code" and "Pull request" rows from that column and nothing else,
        # and orchestrator2 never wrote it — so a repo the agent had genuinely cloned
        # could not be linked from the panel.
        #
        # GUARDED HERE TOO, even though `persist` promises never to raise. The promise
        # is one function's; the cost of it being wrong is the user's whole turn,
        # taken away after the agent had already done the work. A pointer is worth
        # less than that, and this is the layer that knows it.
        try:
            await dev_artifacts.persist(run_id, tenant_id=tenant_id)
        except Exception:  # noqa: BLE001 - a lost pointer must never cost a turn
            logger.exception(
                "orchestrator2 could not record development artifacts (agent=%s "
                "run=%s)", agent_id, run_id,
            )

        try:
            produced = await deliverables.capture(
                agent_id,
                "".join(reply_parts),
                run_id=run_id,
                tenant_id=tenant_id,
                # From the verified `runs` row, like every other project-scoped value
                # on this path. Never from the client frame.
                project_id=project_id,
            )
            if produced:
                yield {"type": "deliverable.ready", "run_id": run_id,
                       "agent": agent_id, "deliverables": produced}
        except Exception as exc:  # noqa: BLE001 - a failed capture must not fail the turn
            # The agent has done its work and the user has read the reply; losing the
            # persistence step is the smaller harm. But it is SURFACED and logged at
            # exception level, never swallowed — a silent `except` here is exactly how
            # the old engine's missing agents went unnoticed for so long.
            logger.exception(
                "orchestrator2 could not persist a deliverable (agent=%s run=%s)",
                agent_id, run_id,
            )
            yield {"type": "error", "agent": agent_id,
                   "message": "The reply could not be saved to Deliverables.",
                   "detail": str(exc)}

    yield {"type": "stream_end"}
