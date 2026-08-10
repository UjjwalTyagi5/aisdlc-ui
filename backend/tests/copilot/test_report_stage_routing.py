"""Fix A — downstream report stages (Code Review / Security / Testing / Deployment /
Documentation) must behave like Design: substantive output streams into the Artifacts
PANEL (artifact.open/delta/end), never dumped as a full chat message. A short reply
(e.g. a clarifying question) below the report-length threshold still goes to chat since
there's no document/report yet to show in the panel.

Covers both stream adapters:
- `_stream_active` (message-based ReAct agents — code_review/security/deployment/docs)
- `_stream_state_machine` (fixed-pipeline adapter — testing)
"""
import json
from types import SimpleNamespace

import pytest

from agents_orchestrator.orchestrator import copilot_api


class _FakeWS:
    """Matches the real _send transport: websocket.send_text(json.dumps(payload))."""
    def __init__(self):
        self.sent = []

    async def send_text(self, text):
        self.sent.append(json.loads(text))


class _Chunk:
    """A minimal stand-in for an AIMessageChunk: str content, no tool call info."""
    def __init__(self, content):
        self.content = content
        self.tool_calls = None
        self.tool_call_chunks = None


class _FakeGraph:
    """Streams pre-baked text chunks; reports a non-empty checkpoint (not first turn)
    so `_stream_active` skips system-prompt/upstream-context construction."""
    def __init__(self, chunks):
        self._chunks = chunks

    async def aget_state(self, config):
        return SimpleNamespace(values={"messages": ["prior turn"]})

    async def astream(self, state, stream_mode=None, config=None):
        for c in self._chunks:
            yield (c,)


REPORT_MD = "# Code Review Report\n\n" + ("Several findings were identified. " * 30)


@pytest.mark.asyncio
async def test_report_stage_body_routes_to_panel_not_chat():
    assert len(REPORT_MD) > copilot_api._REPORT_MIN_LEN
    graph = _FakeGraph([_Chunk(REPORT_MD)])
    ws = _FakeWS()

    reply, artifact_md = await copilot_api._stream_active(
        graph, "review this PR", "run-1", "tenant-1", ws, "code_review",
        model_id="m1", offering_id="o1")

    # The report body is returned as artifact_md, not streamed to chat.
    assert artifact_md == REPORT_MD
    stream_chunks = [m for m in ws.sent if m["type"] == "stream_chunk"]
    assert not any(REPORT_MD[:40] in (m.get("content") or "") for m in stream_chunks)

    types = [m["type"] for m in ws.sent]
    assert "artifact.open" in types
    assert "artifact.delta" in types
    assert "artifact.end" in types
    opens = [m for m in ws.sent if m["type"] == "artifact.open"]
    assert opens[0]["title"] == "Code Review Report"
    assert opens[0]["artifact_id"] == "code_review-report"


@pytest.mark.asyncio
async def test_short_report_stage_reply_stays_chat_only():
    short_reply = "Which repository would you like reviewed?"
    graph = _FakeGraph([_Chunk(short_reply)])
    ws = _FakeWS()

    reply, artifact_md = await copilot_api._stream_active(
        graph, "review", "run-2", "tenant-1", ws, "code_review",
        model_id="m1", offering_id="o1")

    assert artifact_md is None
    assert reply == short_reply
    stream_chunks = [m for m in ws.sent if m["type"] == "stream_chunk"]
    assert any(m.get("content") == short_reply for m in stream_chunks)
    types = [m["type"] for m in ws.sent]
    assert "artifact.open" not in types


@pytest.mark.asyncio
async def test_design_stage_unaffected_by_report_routing():
    """Regression guard: Design's own doc-signature routing must be unchanged."""
    design_md = (
        "## High-Level Design (HLD)\n\nSome content.\n\n"
        "## Low-Level Design (LLD)\n\nMore content.\n"
    )
    graph = _FakeGraph([_Chunk(design_md)])
    ws = _FakeWS()

    reply, artifact_md = await copilot_api._stream_active(
        graph, "design this", "run-3", "tenant-1", ws, "design",
        model_id="m1", offering_id="o1")

    assert artifact_md is not None
    assert "High-Level Design" in artifact_md
    opens = [m for m in ws.sent if m["type"] == "artifact.open"]
    assert opens and opens[0]["title"] == "Design Document"


@pytest.mark.asyncio
async def test_file_tool_completion_triggers_incremental_surface():
    """A file-producing tool (submit_security_review) finishing mid-turn fires the
    on_tool_files callback so the panel shows generated files before the turn ends."""
    from langchain_core.messages import ToolMessage

    calls = []

    async def _on_files():
        calls.append(1)

    class _G(_FakeGraph):
        async def astream(self, state, stream_mode=None, config=None):
            yield (ToolMessage(content="ok", name="submit_security_review", tool_call_id="1"),)
            yield (_Chunk("Security scan complete."),)

    ws = _FakeWS()
    await copilot_api._stream_active(
        _G([]), "scan", "run-6", "tenant-1", ws, "security",
        model_id="m1", offering_id="o1", on_tool_files=_on_files)
    assert calls == [1]


@pytest.mark.asyncio
async def test_non_file_tool_does_not_trigger_incremental_surface():
    from langchain_core.messages import ToolMessage

    calls = []

    async def _on_files():
        calls.append(1)

    class _G(_FakeGraph):
        async def astream(self, state, stream_mode=None, config=None):
            yield (ToolMessage(content="scanned", name="scan_code", tool_call_id="1"),)
            yield (_Chunk("done"),)

    ws = _FakeWS()
    await copilot_api._stream_active(
        _G([]), "scan", "run-7", "tenant-1", ws, "security",
        model_id="m1", offering_id="o1", on_tool_files=_on_files)
    assert calls == []


@pytest.mark.asyncio
async def test_state_machine_adapter_routes_substantial_reply_to_panel(monkeypatch):
    import agents_orchestrator.testing_agent.agents.testing_agent as testing_agent_mod

    async def _fake_resolve(tenant_id, model_id, offering_id=None):
        return None
    monkeypatch.setattr(testing_agent_mod, "_resolve_and_stash_model", _fake_resolve)

    long_report = "# Unit Test Report\n\n" + ("42 passed, 0 failed. " * 30)
    assert len(long_report) > copilot_api._REPORT_MIN_LEN

    class _Graph:
        async def ainvoke(self, state, config=None):
            return {"final_user_message": long_report}

    ws = _FakeWS()
    reply, artifact_md = await copilot_api._stream_state_machine(
        _Graph(), "run tests", "run-4", "tenant-1", ws, "testing",
        model_id="m1", offering_id="o1")

    assert artifact_md == long_report
    assert reply == long_report
    stream_chunks = [m for m in ws.sent if m["type"] == "stream_chunk"]
    assert not any(m.get("content") == long_report for m in stream_chunks)


@pytest.mark.asyncio
async def test_state_machine_adapter_short_reply_still_chat(monkeypatch):
    import agents_orchestrator.testing_agent.agents.testing_agent as testing_agent_mod

    async def _fake_resolve(tenant_id, model_id, offering_id=None):
        return None
    monkeypatch.setattr(testing_agent_mod, "_resolve_and_stash_model", _fake_resolve)

    class _Graph:
        async def ainvoke(self, state, config=None):
            return {"final_user_message": "Please provide the target URL."}

    ws = _FakeWS()
    reply, artifact_md = await copilot_api._stream_state_machine(
        _Graph(), "run tests", "run-5", "tenant-1", ws, "testing",
        model_id="m1", offering_id="o1")

    assert artifact_md is None
    assert reply == "Please provide the target URL."
    stream_chunks = [m for m in ws.sent if m["type"] == "stream_chunk"]
    assert any(m.get("content") == "Please provide the target URL." for m in stream_chunks)
