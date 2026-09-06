import pytest

from agents_orchestrator.orchestrator2 import dispatch, registry as reg
from shared.services import model_resolver as mr


@pytest.fixture(autouse=True)
def _stub_model_resolution(monkeypatch):
    """Every turn now resolves a BYOK model before the graph runs, so these
    dispatch-shape tests would otherwise reach the database. Project-scoping
    behaviour is tested for real in test_byok_project_scoping.py; here we only
    need resolution to succeed so the graph is reached."""
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
        model_id=None, offering_id=None, project_id="proj-1", user_id="u1",
        context="", reason="")]
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
        model_id=None, offering_id=None, project_id="proj-1", user_id="u1",
        context="", reason="")]
    assert fake.seen_config["configurable"]["thread_id"] == "run-42"
    assert any("SYS-PROMPT" in str(getattr(m, "content", m))
               for m in fake.seen_state["messages"])


@pytest.mark.asyncio
async def test_unknown_agent_yields_a_typed_error_not_silence():
    """Fail loudly. The whole point of this phase."""
    events = [e async for e in dispatch.run_agent(
        "nope", text="hi", run_id="r1", tenant_id="t1",
        model_id=None, offering_id=None, project_id="proj-1", user_id="u1",
        context="", reason="")]
    assert any(e["type"] == "error" for e in events)
    assert events[-1]["type"] == "stream_end"


# The nine valid ids per frontend/lib/orchestrator/protocol.ts's OrchestratorAgentId
# enum, pinned to registry.AGENT_IDS so this test cannot drift from the real list.
_VALID_AGENT_IDS = set(reg.AGENT_IDS)

# The event `type` strings the frontend's OrchestratorEvent union accepts for
# what run_agent can emit (frontend/lib/orchestrator/protocol.ts).
_VALID_EVENT_TYPES = {"agent.selected", "stream_chunk", "tool.call", "error", "stream_end"}


@pytest.mark.asyncio
async def test_unknown_agent_error_event_matches_the_frontend_contract_shape():
    """Regression for a Critical review finding: ErrorEvent.agent in
    frontend/lib/orchestrator/protocol.ts is `OrchestratorAgentId.optional()` — an
    enum of the nine valid ids, NOT a free-form string. Yielding the unresolved
    agent id (e.g. "nope") in that field makes Zod's safeParse reject the frame,
    so the client drops it — the one event whose entire job is to announce "that
    agent does not exist" would be the one event guaranteed never to arrive. This
    reproduces the exact silent-failure pattern Phase 2 exists to eliminate, so
    the error event for an unresolved id must carry NO `agent` key at all."""
    events = [e async for e in dispatch.run_agent(
        "nope", text="hi", run_id="r1", tenant_id="t1",
        model_id=None, offering_id=None, project_id="proj-1", user_id="u1",
        context="", reason="")]

    error_events = [e for e in events if e["type"] == "error"]
    assert error_events, "expected an error event for an unknown agent id"
    for e in error_events:
        assert e["type"] == "error"
        assert e.get("agent") is None, (
            f"error event for an unresolved agent id must omit 'agent' (or send it "
            f"null) since it is not one of the nine valid ids; got {e.get('agent')!r}"
        )

    for e in events:
        assert e["type"] in _VALID_EVENT_TYPES, f"unexpected event type: {e['type']!r}"


@pytest.mark.asyncio
async def test_run_agent_emits_only_protocol_shaped_events(monkeypatch):
    """Pin every event run_agent emits, for a resolvable agent, to the frontend
    contract: agent.selected.agent must be one of the nine valid ids, and every
    event's `type` must be a string the OrchestratorEvent union accepts."""
    fake = _FakeStreamGraph()
    monkeypatch.setitem(
        reg.REGISTRY, "design",
        reg.AgentCapability(agent_id="design", load_graph=lambda: fake,
                            load_prompt=lambda: "SYS", mode="stream"),
    )
    events = [e async for e in dispatch.run_agent(
        "design", text="hi", run_id="r1", tenant_id="t1",
        model_id=None, offering_id=None, project_id="proj-1", user_id="u1",
        context="", reason="")]

    for e in events:
        assert e["type"] in _VALID_EVENT_TYPES, f"unexpected event type: {e['type']!r}"

    selected = [e for e in events if e["type"] == "agent.selected"]
    assert selected, "expected an agent.selected event"
    for e in selected:
        assert e["agent"] in _VALID_AGENT_IDS, (
            f"agent.selected.agent must be one of the nine valid ids; got {e['agent']!r}"
        )
