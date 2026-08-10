import json

import pytest
from types import SimpleNamespace
from agents_orchestrator.orchestrator import copilot_api


class _FakeGraph:
    """Records the state it was invoked with; returns a canned final state."""
    def __init__(self, final_state):
        self._final = final_state
        self.invoked_with = None

    async def aget_state(self, config):
        return SimpleNamespace(values={})

    async def ainvoke(self, state, config=None):
        self.invoked_with = state
        return self._final


class _FakeWS:
    """Matches the real _send transport: websocket.send_text(json.dumps(payload))."""
    def __init__(self):
        self.sent = []

    async def send_text(self, text):
        self.sent.append(json.loads(text))


@pytest.mark.asyncio
async def test_state_machine_adapter_invokes_with_user_prompt_and_streams_final_message(monkeypatch):
    import agents_orchestrator.testing_agent.agents.testing_agent as testing_agent_mod

    resolve_calls = []

    async def _fake_resolve(tenant_id, model_id, offering_id=None):
        resolve_calls.append((tenant_id, model_id, offering_id))
        return None

    monkeypatch.setattr(testing_agent_mod, "_resolve_and_stash_model", _fake_resolve)

    graph = _FakeGraph({"final_user_message": "Here is your unit test report. 42 passed."})
    ws = _FakeWS()
    reply, artifact = await copilot_api._stream_state_machine(
        graph, "do unit testing", "run-1", "tenant-1", ws, "testing",
        model_id="m1", offering_id="o1")
    # invoked with user_prompt, NOT messages
    assert graph.invoked_with["user_prompt"] == "do unit testing"
    assert "messages" not in graph.invoked_with
    assert graph.invoked_with["tenant_id"] == "tenant-1"
    assert graph.invoked_with["model_id"] == "m1"
    # C1: per-turn fields reset in the input state so a checkpointed value from a
    # prior turn (e.g. a stale final_user_message) can't mask this turn's summary.
    assert graph.invoked_with["final_user_message"] is None
    assert graph.invoked_with["classified_intent"] == "unsupported"
    assert graph.invoked_with["error_message"] is None
    assert graph.invoked_with["final_outputs"] == {}
    # final message streamed to chat + returned
    assert reply == "Here is your unit test report. 42 passed."
    assert artifact is None
    types = [m["type"] for m in ws.sent]
    assert "stream_chunk" in types and "stream_end" in types
    assert any(m.get("content") == "Here is your unit test report. 42 passed."
               for m in ws.sent if m["type"] == "stream_chunk")
    # I2: the BYOK model is resolved+stashed before the graph is invoked.
    assert resolve_calls == [("tenant-1", "m1", "o1")]


@pytest.mark.asyncio
async def test_testing_graph_carries_state_across_turns():
    from langgraph.checkpoint.memory import MemorySaver
    from agents_orchestrator.testing_agent.agents.testing_agent import graph_builder
    app = graph_builder.compile(checkpointer=MemorySaver())
    cfg = {"configurable": {"thread_id": "reentry-1"}, "recursion_limit": 100}
    # turn 1: greeting-ish prompt (no work); state is checkpointed
    await app.ainvoke({"user_prompt": "hello", "tenant_id": "t"}, config=cfg)
    st1 = await app.aget_state(cfg)
    assert st1 is not None
    # turn 2: a functional request without URL should set awaiting_scope in state
    await app.ainvoke({"user_prompt": "run functional testing", "tenant_id": "t"}, config=cfg)
    st2 = await app.aget_state(cfg)
    vals = getattr(st2, "values", {}) or {}
    # the agent asked for a URL (awaiting_scope) OR routed to a scope prompt — either proves
    # user_prompt landed (not the greeting no-op that the {messages} bug caused)
    assert vals.get("awaiting_scope") is True or "url" in (vals.get("final_user_message") or "").lower()
