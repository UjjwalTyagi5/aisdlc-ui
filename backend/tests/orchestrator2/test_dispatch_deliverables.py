"""Capture happens inside the turn, before it is declared finished.

Ordering is the point. `stream_end` is yielded from inside `run_agent`, so capturing
in `ws.py` after its loop would land `deliverable.ready` AFTER the client had already
been told the turn was over — and the panel would fill in after the composer came
back, which reads as a document arriving from nowhere.

The fakes here are the ones `test_dispatch_streaming.py` already uses — same stubbed
model resolution, same scripted graph — so a change to the dispatch loop breaks both
files together rather than leaving this one passing against a shape that no longer
exists.
"""
import pytest
from langchain_core.messages import AIMessageChunk

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
    def __init__(self, messages):
        self._messages = messages

    async def astream(self, state, stream_mode=None, config=None):
        for message in self._messages:
            yield (message, {})


async def _turn(monkeypatch, reply: str, *, project_id: str = "p1") -> list[dict]:
    """Run one `security` turn whose agent says exactly `reply`."""
    monkeypatch.setitem(
        reg.REGISTRY, "security",
        reg.AgentCapability(
            agent_id="security",
            load_graph=lambda: _ScriptedGraph([AIMessageChunk(content=reply)]),
            load_prompt=lambda: "SYS",
            mode="stream",
        ),
    )
    return [e async for e in dispatch.run_agent(
        "security", text="hi", run_id="r1", tenant_id="t1",
        model_id=None, offering_id=None, project_id=project_id,
        context="", reason="")]


def _events_of(events, kind):
    return [e for e in events if e.get("type") == kind]


@pytest.mark.asyncio
async def test_deliverable_ready_precedes_stream_end(monkeypatch):
    async def _capture(agent_id, reply_text, *, run_id, tenant_id, project_id):
        return [{"id": "d1", "agent": agent_id, "kind": "markdown",
                 "title": "Security Report", "content": reply_text}]

    monkeypatch.setattr(dispatch.deliverables, "capture", _capture)
    events = await _turn(monkeypatch, "x" * 400)
    types = [e["type"] for e in events]
    assert "deliverable.ready" in types
    assert types.index("deliverable.ready") < types.index("stream_end")


@pytest.mark.asyncio
async def test_the_event_names_the_agent_and_the_run(monkeypatch):
    async def _capture(agent_id, reply_text, *, run_id, tenant_id, project_id):
        return [{"id": "d1", "agent": agent_id, "kind": "markdown",
                 "title": "T", "content": "c"}]

    monkeypatch.setattr(dispatch.deliverables, "capture", _capture)
    events = await _turn(monkeypatch, "x" * 400)
    ready = _events_of(events, "deliverable.ready")[0]
    assert ready["agent"] == "security"
    assert ready["run_id"] == "r1"
    assert len(ready["deliverables"]) == 1


@pytest.mark.asyncio
async def test_capture_receives_exactly_what_the_user_saw(monkeypatch):
    """Accumulated from the EVENTS, not from the graph — so what is stored is what
    was displayed, even if the graph's internal state says otherwise."""
    seen = {}

    async def _capture(agent_id, reply_text, *, run_id, tenant_id, project_id):
        seen["text"] = reply_text
        seen["project_id"] = project_id
        seen["tenant_id"] = tenant_id
        return []

    monkeypatch.setattr(dispatch.deliverables, "capture", _capture)
    await _turn(monkeypatch, "hello world " * 40, project_id="p-1")
    assert seen["text"] == "hello world " * 40
    assert seen["project_id"] == "p-1", "project scope must reach capture"
    assert seen["tenant_id"] == "t1"


@pytest.mark.asyncio
async def test_a_failed_capture_does_not_fail_the_turn(monkeypatch):
    """The agent did the work and the user read it. Losing the persistence step is a
    smaller harm than losing the reply — but it is SURFACED, never swallowed."""
    async def _boom(agent_id, reply_text, *, run_id, tenant_id, project_id):
        raise dispatch.deliverables.DeliverableWriteError("disk on fire")

    monkeypatch.setattr(dispatch.deliverables, "capture", _boom)
    events = await _turn(monkeypatch, "x" * 400)
    types = [e["type"] for e in events]
    assert types[-1] == "stream_end", "the turn must still end cleanly"
    assert _events_of(events, "error"), "a failed capture must be visible, not silent"


@pytest.mark.asyncio
async def test_the_reply_still_reaches_the_user_when_capture_fails(monkeypatch):
    """The reply is the thing that matters. It must not be lost with the write."""
    async def _boom(agent_id, reply_text, *, run_id, tenant_id, project_id):
        raise dispatch.deliverables.DeliverableWriteError("disk on fire")

    monkeypatch.setattr(dispatch.deliverables, "capture", _boom)
    events = await _turn(monkeypatch, "y" * 400)
    text = "".join(e["content"] for e in _events_of(events, "stream_chunk"))
    assert text == "y" * 400


@pytest.mark.asyncio
async def test_nothing_is_emitted_when_the_turn_produced_no_deliverable(monkeypatch):
    """An empty `deliverable.ready` would make the panel flash a heading with nothing
    under it, which reads as a document that failed to load."""
    async def _capture(agent_id, reply_text, *, run_id, tenant_id, project_id):
        return []

    monkeypatch.setattr(dispatch.deliverables, "capture", _capture)
    events = await _turn(monkeypatch, "ok")
    assert not _events_of(events, "deliverable.ready")


@pytest.mark.asyncio
async def test_a_failed_turn_captures_nothing(monkeypatch):
    """Capturing a partial reply from a failed turn would file a truncated document
    under the agent's heading, where nothing distinguishes it from a complete one."""
    called = []

    async def _capture(agent_id, reply_text, *, run_id, tenant_id, project_id):
        called.append(reply_text)
        return []

    monkeypatch.setattr(dispatch.deliverables, "capture", _capture)

    class _Exploding:
        async def astream(self, state, stream_mode=None, config=None):
            yield (AIMessageChunk(content="partial " * 40), {})
            raise RuntimeError("graph died")

    monkeypatch.setitem(
        reg.REGISTRY, "security",
        reg.AgentCapability(agent_id="security", load_graph=lambda: _Exploding(),
                            load_prompt=lambda: "SYS", mode="stream"),
    )
    events = [e async for e in dispatch.run_agent(
        "security", text="hi", run_id="r1", tenant_id="t1",
        model_id=None, offering_id=None, project_id="p1", context="", reason="")]
    assert _events_of(events, "error"), "the failure itself must still be reported"
    assert called == [], "a failed turn must not persist its partial output"
