"""A Code Review turn must announce that it is over — above all when it failed.

WHY THIS FILE EXISTS. A real run on a model whose Azure key had been revoked showed the
error in the drawer and then sat on "Agent is working… 121s" with the composer locked.
app/api/chat/route.ts ends a run on activity_update{complete} or
agent_completed{success: false}; the failure path sent neither. The same turn also
printed the provider's own exception text, and a failure after some streamed text was
swallowed and announced as "Review complete".
"""
from __future__ import annotations

import json
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import AsyncMock

import litellm
import pytest
from langchain_core.messages import AIMessageChunk

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents_orchestrator.code_review_agent import code_review_agent_api as api  # noqa: E402
from agents_orchestrator.code_review_agent.config.session_state import clear_session  # noqa: E402


class _Manager:
    """Records every frame the turn sent, in order, however it was sent."""

    def __init__(self) -> None:
        self.frames: List[Dict[str, Any]] = []

    async def broadcast(self, payload: Dict[str, Any]) -> None:
        self.frames.append(payload)

    async def send_personal_message(self, message: str, _websocket) -> None:
        self.frames.append(json.loads(message))

    async def send_agent_response(self, agent_name: str, message: str, session_id: str) -> None:
        self.frames.append({"type": "agent_response", "agent_name": agent_name,
                            "message": message, "session_id": session_id})

    def types(self) -> List[str]:
        return [f.get("type") for f in self.frames]

    def said(self) -> List[str]:
        return [f.get("message", "") for f in self.frames if f.get("type") == "agent_response"]


def _auth_error() -> Exception:
    return litellm.exceptions.AuthenticationError(
        message="AzureException AuthenticationError - Access denied due to invalid subscription key",
        llm_provider="azure", model="gpt-5-mini",
    )


@pytest.fixture
def turn(monkeypatch):
    """Drive one WS turn with access, tracing, MCP and the graph stubbed out."""
    mgr = _Manager()
    persist = AsyncMock()
    monkeypatch.setattr(api, "manager", mgr)

    @asynccontextmanager
    async def _db(_tenant):
        yield None

    monkeypatch.setattr(api, "get_db_session_for_tenant", _db)
    monkeypatch.setattr(api, "assert_agent_access_for_chat", AsyncMock(return_value="p1"))
    monkeypatch.setattr(api, "get_prepared", lambda *_a, **_k: None)
    monkeypatch.setattr(api, "agent_trace", AsyncMock(return_value=([], {})))
    monkeypatch.setattr(api, "_load_mcp_tools", AsyncMock(return_value=[]))
    monkeypatch.setattr(api, "resolve_agent_turn", AsyncMock(return_value=("prompt", [])))
    monkeypatch.setattr(api, "_persist_review_to_run", persist)

    async def _run(chunks=(), boom=None):
        class _App:
            @staticmethod
            async def astream(_state, **_kwargs):
                for text in chunks:
                    yield (AIMessageChunk(content=text), {})
                if boom is not None:
                    raise boom

        monkeypatch.setattr(api, "review_app", _App)
        clear_session("s1")
        await api._process_ws_message(
            {"type": "user_message_with_files", "session_id": "s1", "project_id": "p1",
             "task_intent": "review it"},
            websocket=object(), user_id="u1", tenant_id="t1",
        )
        return mgr, persist

    yield _run
    clear_session("s1")


def _failed(frames) -> bool:
    return any(f.get("type") == "agent_completed" and f.get("success") is False for f in frames)


def _complete(frames) -> List[str]:
    return [f["activity"]["message"] for f in frames
            if f.get("type") == "activity_update" and f.get("activity", {}).get("type") == "complete"]


@pytest.mark.unit
async def test_a_model_failure_ends_the_run_as_failed(turn):
    mgr, _ = await turn(boom=_auth_error())
    assert _failed(mgr.frames), mgr.types()
    assert "stream_end" in mgr.types()
    assert _complete(mgr.frames) == ["Review failed"]


@pytest.mark.unit
async def test_the_user_is_told_who_fixes_it_not_the_providers_text(turn):
    mgr, _ = await turn(boom=_auth_error())
    [said] = mgr.said()
    assert "rejected the configured credential" in said
    assert "subscription key" not in said


@pytest.mark.unit
async def test_a_failure_after_some_text_is_not_announced_as_complete(turn):
    mgr, persist = await turn(chunks=["Reading src/index.js…"], boom=_auth_error())
    assert _failed(mgr.frames), mgr.types()
    assert "Review complete" not in _complete(mgr.frames)
    # A review submitted before the model failed is still saved.
    persist.assert_awaited_once()


@pytest.mark.unit
async def test_the_platforms_own_errors_are_shown_as_they_are(turn):
    mgr, _ = await turn(boom=RuntimeError("git fetch failed: repository not found"))
    assert mgr.said() == ["An error occurred: git fetch failed: repository not found"]
    assert _failed(mgr.frames)


@pytest.mark.unit
async def test_a_successful_turn_ends_as_complete_and_not_failed(turn):
    mgr, persist = await turn(chunks=["Review submitted."])
    assert not _failed(mgr.frames)
    types = mgr.types()
    assert types.index("stream_end") < len(types) - 1
    assert _complete(mgr.frames) == ["Review complete"]
    persist.assert_awaited_once()
