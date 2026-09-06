"""What reaches the client while an agent runs: its words, and its tool activity.

Two defects live in the `stream_mode="messages"` loop, and every earlier test in
`test_dispatch.py` is blind to both because they all fake the graph with a plain
string `content`:

  1. `chunk.content` is a LIST OF TYPED BLOCKS for Anthropic, not a string. `json.dumps`
     accepts a list, so nothing raises on the way out; the frame reaches the browser and
     fails `StreamChunkEvent.content: z.string()` in
     `frontend/lib/orchestrator/protocol.ts`, so Zod drops it. The user watches an agent
     produce nothing at all. Same shape as the `ErrorEvent.agent` frame this branch
     already lost once — this time on the one event type carrying every word the user
     reads.
  2. `stream_mode="messages"` yields `ToolMessage`s too, whose `.content` is tool OUTPUT.
     Streamed as-is it appears in the transcript as the agent's own prose.

And `tool.call` is emitted here for the first time: it is declared in the protocol and
the Activity tab consumes it, so leaving it unimplemented left a declared interface
hollow.
"""
import json

import pytest
from langchain_core.messages import AIMessageChunk, ToolMessage

from agents_orchestrator.orchestrator2 import dispatch, registry as reg
from shared.services import model_resolver as mr


@pytest.fixture(autouse=True)
def _stub_model_resolution(monkeypatch):
    async def _fake_resolve(tenant_id, requested_model_id=None, **kwargs):
        return mr.ResolvedModel(
            provider="anthropic", litellm_provider="anthropic", model="m",
            api_key="k", base_url=None, alias="tenant:t1:p1",
        )

    monkeypatch.setattr(dispatch, "resolve_model_for_run", _fake_resolve)
    mr.set_resolved_model(None)
    mr.set_run_project(None)
    yield
    mr.set_resolved_model(None)
    mr.set_run_project(None)


class _ScriptedGraph:
    """Yields exactly the messages given, in `stream_mode="messages"` shape."""

    def __init__(self, messages):
        self._messages = messages

    async def astream(self, state, stream_mode=None, config=None):
        for message in self._messages:
            yield (message, {})


def _install(monkeypatch, messages):
    monkeypatch.setitem(
        reg.REGISTRY, "design",
        reg.AgentCapability(agent_id="design", load_graph=lambda: _ScriptedGraph(messages),
                            load_prompt=lambda: "SYS", mode="stream"),
    )


async def _run():
    return [e async for e in dispatch.run_agent(
        "design", text="hi", run_id="r1", tenant_id="t1",
        model_id=None, offering_id=None, project_id="p1",
        context="", reason="")]


def _chunks(events):
    return [e for e in events if e["type"] == "stream_chunk"]


def _tool_calls(events):
    return [e for e in events if e["type"] == "tool.call"]


# ── defect 1: block-list content ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_block_list_content_reaches_the_client_as_a_string(monkeypatch):
    """The regression. Anthropic streams `[{"type": "text", "text": ...}]`; a list in
    `content` fails the frontend's `z.string()` and the frame is dropped in the
    browser, so the agent appears to say nothing."""
    _install(monkeypatch, [
        AIMessageChunk(content=[{"type": "text", "text": "Hello ", "index": 0}]),
        AIMessageChunk(content=[{"type": "text", "text": "world", "index": 0}]),
    ])
    events = await _run()

    for event in _chunks(events):
        assert isinstance(event["content"], str), (
            f"content must be a string for StreamChunkEvent; got {type(event['content'])}"
        )
    assert "".join(e["content"] for e in _chunks(events)) == "Hello world"


@pytest.mark.asyncio
async def test_streamed_text_keeps_the_whitespace_that_joins_tokens(monkeypatch):
    """`router._content_text` strips, and MUST NOT be reused here.

    Stripping is right for a routing decision (whitespace changes no meaning) and
    destroys a stream: "Hello " + "world" arrives as "Helloworld". The two callers
    genuinely need different functions, so this test exists to stop a future tidy-up
    unifying them.
    """
    _install(monkeypatch, [
        AIMessageChunk(content=[{"type": "text", "text": "Hello ", "index": 0}]),
        AIMessageChunk(content="world"),
    ])
    events = await _run()
    assert "".join(e["content"] for e in _chunks(events)) == "Hello world"


@pytest.mark.asyncio
async def test_a_non_text_block_contributes_no_text_but_breaks_nothing(monkeypatch):
    _install(monkeypatch, [
        AIMessageChunk(content=[{"type": "image", "source": {}}]),
        AIMessageChunk(content=[{"type": "text", "text": "after", "index": 1}]),
    ])
    events = await _run()
    assert "".join(e["content"] for e in _chunks(events)) == "after"
    assert events[-1]["type"] == "stream_end"


@pytest.mark.asyncio
async def test_every_stream_chunk_survives_a_json_round_trip(monkeypatch):
    """`ws._send` calls `json.dumps`, which accepts a list happily — which is why this
    defect reached the browser instead of raising. Assert the STRING type, not merely
    that it encodes."""
    _install(monkeypatch, [
        AIMessageChunk(content=[{"type": "text", "text": "x", "index": 0}]),
    ])
    for event in _chunks(await _run()):
        assert isinstance(json.loads(json.dumps(event))["content"], str)


# ── defect 2: tool output is not the agent's prose ───────────────────────────


@pytest.mark.asyncio
async def test_tool_output_is_never_streamed_as_the_agents_own_words(monkeypatch):
    _install(monkeypatch, [
        AIMessageChunk(content="Reading the repo. "),
        ToolMessage(content="SECRET-TOOL-OUTPUT", tool_call_id="tc1", name="read_repo"),
        AIMessageChunk(content="Done."),
    ])
    events = await _run()
    text = "".join(e["content"] for e in _chunks(events))
    assert "SECRET-TOOL-OUTPUT" not in text, (
        "a ToolMessage's content is tool OUTPUT — streaming it puts it in the "
        "transcript as if the agent had said it"
    )
    assert text == "Reading the repo. Done."


# ── tool.call ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_finished_tool_becomes_a_done_tool_call_event(monkeypatch):
    _install(monkeypatch, [
        ToolMessage(content="ok", tool_call_id="tc1", name="read_repo"),
    ])
    calls = _tool_calls(await _run())
    assert len(calls) == 1
    assert calls[0]["name"] == "read_repo"
    assert calls[0]["status"] == "done"
    assert calls[0]["run_id"] == "r1"


@pytest.mark.asyncio
async def test_a_started_tool_becomes_a_running_tool_call_event(monkeypatch):
    _install(monkeypatch, [
        AIMessageChunk(content="", tool_call_chunks=[
            {"name": "run_tests", "args": "{}", "id": "tc9", "index": 0,
             "type": "tool_call_chunk"},
        ]),
    ])
    calls = _tool_calls(await _run())
    assert [(c["name"], c["status"]) for c in calls] == [("run_tests", "running")]


@pytest.mark.asyncio
async def test_a_tool_call_chunk_announces_each_tool_once(monkeypatch):
    """Providers split one tool call across several chunks; each carries the same id
    and only the first carries the name. One `running` per tool, not one per chunk."""
    _install(monkeypatch, [
        AIMessageChunk(content="", tool_call_chunks=[
            {"name": "run_tests", "args": '{"pa', "id": "tc9", "index": 0,
             "type": "tool_call_chunk"},
        ]),
        AIMessageChunk(content="", tool_call_chunks=[
            {"name": None, "args": 'th": "x"}', "id": "tc9", "index": 0,
             "type": "tool_call_chunk"},
        ]),
    ])
    calls = _tool_calls(await _run())
    assert [(c["name"], c["status"]) for c in calls] == [("run_tests", "running")]


@pytest.mark.asyncio
async def test_a_tool_whose_name_repeats_in_every_chunk_is_announced_once(monkeypatch):
    """The case the test above does NOT cover, found by mutation.

    Removing the `key in announced` dedupe left the whole suite green, because the
    other test's continuation chunk carries `name: None` and is filtered by the `not
    name` guard before dedupe is ever consulted. Providers differ: some send the name
    only on the first chunk of a tool call, others repeat it on every one. Only the
    second shape exercises the dedupe, and without it a single tool call streamed in
    twenty chunks puts twenty entries in the Activity tab.
    """
    _install(monkeypatch, [
        AIMessageChunk(content="", tool_call_chunks=[
            {"name": "run_tests", "args": '{"pa', "id": "tc9", "index": 0,
             "type": "tool_call_chunk"},
        ]),
        AIMessageChunk(content="", tool_call_chunks=[
            {"name": "run_tests", "args": 'th": "x"}', "id": "tc9", "index": 0,
             "type": "tool_call_chunk"},
        ]),
    ])
    calls = _tool_calls(await _run())
    assert [(c["name"], c["status"]) for c in calls] == [("run_tests", "running")], (
        "one `running` per tool, not per chunk"
    )


@pytest.mark.asyncio
async def test_two_different_tools_are_both_announced(monkeypatch):
    """The dedupe must key on the tool call id, not collapse distinct tools."""
    _install(monkeypatch, [
        AIMessageChunk(content="", tool_call_chunks=[
            {"name": "read_repo", "args": "{}", "id": "tc1", "index": 0,
             "type": "tool_call_chunk"},
        ]),
        AIMessageChunk(content="", tool_call_chunks=[
            {"name": "run_tests", "args": "{}", "id": "tc2", "index": 1,
             "type": "tool_call_chunk"},
        ]),
    ])
    calls = _tool_calls(await _run())
    assert [c["name"] for c in calls] == ["read_repo", "run_tests"]


@pytest.mark.asyncio
async def test_tool_call_matches_the_frontend_schema_exactly(monkeypatch):
    """`ToolCallEvent.name` is `z.string()` with NO default, so a frame without a
    usable `name` fails safeParse and is dropped in the browser — silently, which is
    the failure mode this engine exists to remove. An unnamed tool is announced under
    a placeholder rather than as a frame that cannot arrive."""
    _install(monkeypatch, [
        ToolMessage(content="ok", tool_call_id="tc1", name=None),
    ])
    for call in _tool_calls(await _run()):
        assert set(call) <= {"type", "run_id", "name", "status"}
        assert isinstance(call.get("name"), str) and call["name"], (
            "name must be a non-empty string or the frontend drops the frame"
        )
        assert call["status"] in ("running", "done")


@pytest.mark.asyncio
async def test_ordinary_text_emits_no_tool_call(monkeypatch):
    _install(monkeypatch, [AIMessageChunk(content="just talking")])
    assert _tool_calls(await _run()) == []


@pytest.mark.asyncio
async def test_stream_end_is_still_last_with_tool_activity(monkeypatch):
    _install(monkeypatch, [
        AIMessageChunk(content="a", tool_call_chunks=[
            {"name": "t", "args": "{}", "id": "1", "index": 0, "type": "tool_call_chunk"},
        ]),
        ToolMessage(content="r", tool_call_id="1", name="t"),
        AIMessageChunk(content="b"),
    ])
    events = await _run()
    assert events[0]["type"] == "agent.selected"
    assert events[-1]["type"] == "stream_end"


# ── an unreadable content shape is loud, not quietly empty ───────────────────


@pytest.mark.asyncio
async def test_an_unreadable_content_shape_becomes_a_typed_error_not_silence(monkeypatch):
    """The first version of `_stream_text` returned "" for a shape it did not
    recognise, which would have been a REGRESSION in visibility: the code before it
    forwarded the value to `json.dumps`, which raises on an object it cannot encode,
    so the socket reported a dropped frame. Returning "" instead turns an unknown
    provider shape into an agent that silently says less than it said."""
    _install(monkeypatch, [
        AIMessageChunk(content="before "),
        type("M", (), {"content": object()})(),
        AIMessageChunk(content="never reached"),
    ])
    events = await _run()

    assert "".join(e["content"] for e in _chunks(events)) == "before ", (
        "text streamed before the bad chunk must still have been delivered"
    )
    errors = [e for e in events if e["type"] == "error"]
    assert errors, "an unreadable chunk shape must be reported, not swallowed"
    assert errors[0]["agent"] == "design"
    assert events[-1]["type"] == "stream_end"


@pytest.mark.asyncio
@pytest.mark.parametrize("content", [None, "", [], [{"type": "image", "source": {}}]])
async def test_understood_shapes_carrying_no_text_are_not_errors(monkeypatch, content):
    """`None` is an ABSENCE of text — the canonical content of a chunk that carried
    only tool calls — not an unreadable shape. Only an unrecognised container raises."""
    _install(monkeypatch, [type("M", (), {"content": content})()])
    events = await _run()
    assert [e for e in events if e["type"] == "error"] == []
    assert _chunks(events) == []
    assert events[-1]["type"] == "stream_end"


# ── an invoke-mode agent that produces nothing must say so ───────────────────


class _InvokeGraph:
    def __init__(self, final_state):
        self._final_state = final_state

    async def ainvoke(self, state, config=None):
        return self._final_state


def _install_invoke(monkeypatch, final_state):
    monkeypatch.setitem(
        reg.REGISTRY, "testing",
        reg.AgentCapability(agent_id="testing", load_graph=lambda: _InvokeGraph(final_state),
                            load_prompt=lambda: "SYS", mode="invoke"),
    )


async def _run_testing():
    return [e async for e in dispatch.run_agent(
        "testing", text="run the tests", run_id="r1", tenant_id="t1",
        model_id=None, offering_id=None, project_id="p1", context="", reason="")]


@pytest.mark.asyncio
@pytest.mark.parametrize("final_state", [{}, {"final_user_message": ""}, None])
async def test_an_invoke_agent_that_returns_nothing_reports_it(monkeypatch, final_state):
    """The turn used to end in COMPLETE silence: an empty `stream_chunk` is skipped by
    the client, and the bubble that never received a token is then removed — so the
    user saw their message, "the Testing agent is answering", and nothing else. No
    reply, no error, composer handed back."""
    _install_invoke(monkeypatch, final_state)
    events = await _run_testing()

    assert _chunks(events) == [], "an empty chunk is worse than no chunk"
    errors = [e for e in events if e["type"] == "error"]
    assert errors, "a turn that produced nothing must say so"
    assert errors[0]["agent"] == "testing"
    assert events[-1]["type"] == "stream_end"


@pytest.mark.asyncio
async def test_an_invoke_agent_with_a_reply_still_streams_it(monkeypatch):
    _install_invoke(monkeypatch, {"final_user_message": "All 42 tests passed."})
    events = await _run_testing()
    assert [e["content"] for e in _chunks(events)] == ["All 42 tests passed."]
    assert [e for e in events if e["type"] == "error"] == []
