"""The planner WS turn must announce that it is over.

WHY THIS FILE EXISTS. app/api/chat/route.ts ends a run on
activity_update{type: "complete"} and arms its idle fallback on stream_end. The PM
route emitted NEITHER, and nothing closes the socket from the server side, so a turn
that answered perfectly still left the composer disabled and the run hanging open.

Nothing in-process caught it: the REST route was fine, the graph was fine, the answer
was correct. Only driving the real socket showed the client still waiting. These tests
pin the frame contract so the next agent route cannot lose it quietly.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents_orchestrator.pm_agent import pm_agent_api as api  # noqa: E402


class _Manager:
    """Records what the turn broadcast, in order."""

    def __init__(self) -> None:
        self.frames: List[Dict[str, Any]] = []

    async def broadcast(self, payload: Dict[str, Any]) -> None:
        self.frames.append(payload)

    def types(self) -> List[str]:
        return [f.get("type") for f in self.frames]


def _terminal(frames: List[Dict[str, Any]]) -> bool:
    """The one frame the BFF actually closes a run on."""
    return any(
        f.get("type") == "activity_update"
        and isinstance(f.get("activity"), dict)
        and f["activity"].get("type") == "complete"
        for f in frames
    )


@pytest.fixture
def turn(monkeypatch):
    """Drive one WS turn with the graph and the context helpers stubbed out."""
    mgr = _Manager()
    monkeypatch.setattr(api, "manager", mgr)
    for name in ("set_session_id", "set_user_id", "set_tenant_id", "set_project_id",
                 "set_run_id", "set_provider_kind"):
        monkeypatch.setattr(api, name, lambda *_a, **_k: None)

    async def _config(*_a, **_k):
        return {"configurable": {"thread_id": "t"}}

    monkeypatch.setattr(api, "_run_config", _config)
    api._initialized_sessions.discard("s1")

    async def _run(answer=None, boom=None):
        class _App:
            @staticmethod
            async def ainvoke(_state, _cfg):
                if boom is not None:
                    raise boom
                return {"messages": [type("M", (), {"content": answer})()]}

        monkeypatch.setattr(api, "planning_app", _App)
        monkeypatch.setattr(api, "_last_reply", lambda _m: answer or "")
        await api._process_turn_ws(
            {"type": "user_message_with_files", "task_intent": "plan it",
             "session_id": "s1"},
            user_id="u1", tenant_id="", session_id="s1",
        )
        return mgr

    return _run


@pytest.mark.unit
async def test_a_successful_turn_ends_the_run(turn):
    """Without this frame the run never closes and the composer stays disabled."""
    mgr = await turn(answer="Sprint 1 holds 18 of 20 points.")
    assert _terminal(mgr.frames)


@pytest.mark.unit
async def test_a_successful_turn_emits_stream_end(turn):
    """The BFF arms its idle safety fallback here. It is the only thing standing
    between a missing terminal and a run that hangs forever."""
    mgr = await turn(answer="ok")
    assert "stream_end" in mgr.types()


@pytest.mark.unit
async def test_the_answer_arrives_before_the_turn_is_declared_over(turn):
    """A terminal frame that overtakes the content is a run that closes on an empty
    transcript."""
    types = (await turn(answer="ok")).types()
    assert types.index("agent_response") < types.index("stream_end")
    assert types[-1] == "activity_update"


@pytest.mark.unit
async def test_a_failed_turn_still_ends_the_run(turn):
    """The case where a stuck composer is least forgivable: the user cannot even
    retry."""
    mgr = await turn(boom=RuntimeError("model exploded"))
    assert _terminal(mgr.frames)
    assert "stream_end" in mgr.types()


@pytest.mark.unit
async def test_a_failed_turn_is_reported_as_failed_not_approved(turn):
    """agent_completed{success: false} is what the BFF maps to a failed run. Without
    it a turn that errored is recorded as a successful one."""
    mgr = await turn(boom=RuntimeError("model exploded"))
    failed = [f for f in mgr.frames
              if f.get("type") == "agent_completed" and f.get("success") is False]
    assert failed, mgr.types()


@pytest.mark.unit
async def test_the_user_is_told_what_went_wrong(turn):
    mgr = await turn(boom=RuntimeError("model exploded"))
    said = [f.get("message", "") for f in mgr.frames if f.get("type") == "agent_response"]
    assert said and "error occurred" in said[0].lower()


@pytest.mark.unit
async def test_an_exception_does_not_escape_the_turn(turn):
    """It would kill the receive loop and drop the socket mid-conversation."""
    await turn(boom=ValueError("bad"))  # must not raise
