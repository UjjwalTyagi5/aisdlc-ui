import pytest

from agents_orchestrator.orchestrator2 import dispatch, registry as reg


class _FakeStreamGraph:
    def __init__(self):
        self.seen_state = None
        self.seen_config = None

    async def astream(self, state, stream_mode=None, config=None):
        self.seen_state, self.seen_config = state, config
        for part in ("Hello ", "world"):
            yield (type("M", (), {"content": part})(), {})


@pytest.mark.asyncio
async def test_run_agent_announces_the_agent_before_any_text(monkeypatch):
    """agent.selected must arrive first. The old engine switched agents silently,
    so a wrong choice was invisible until the answer made no sense."""
    fake = _FakeStreamGraph()
    monkeypatch.setitem(
        reg.REGISTRY, "design",
        reg.AgentCapability(agent_id="design", load_graph=lambda: fake,
                            load_prompt=lambda: "SYS", mode="stream"),
    )
    events = [e async for e in dispatch.run_agent(
        "design", text="hi", run_id="r1", tenant_id="t1",
        model_id=None, offering_id=None)]
    assert events[0]["type"] == "agent.selected"
    assert events[0]["agent"] == "design"
    assert events[-1]["type"] == "stream_end"
    assert "".join(e.get("content", "") for e in events if e["type"] == "stream_chunk") == "Hello world"


@pytest.mark.asyncio
async def test_run_agent_passes_thread_id_and_system_prompt(monkeypatch):
    fake = _FakeStreamGraph()
    monkeypatch.setitem(
        reg.REGISTRY, "design",
        reg.AgentCapability(agent_id="design", load_graph=lambda: fake,
                            load_prompt=lambda: "SYS-PROMPT", mode="stream"),
    )
    _ = [e async for e in dispatch.run_agent(
        "design", text="hi", run_id="run-42", tenant_id="t1",
        model_id=None, offering_id=None)]
    assert fake.seen_config["configurable"]["thread_id"] == "run-42"
    assert any("SYS-PROMPT" in str(getattr(m, "content", m))
               for m in fake.seen_state["messages"])


@pytest.mark.asyncio
async def test_unknown_agent_yields_a_typed_error_not_silence():
    """Fail loudly. The whole point of this phase."""
    events = [e async for e in dispatch.run_agent(
        "nope", text="hi", run_id="r1", tenant_id="t1",
        model_id=None, offering_id=None)]
    assert any(e["type"] == "error" for e in events)
    assert events[-1]["type"] == "stream_end"
