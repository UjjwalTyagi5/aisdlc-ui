"""Regression tests for final-review.md I2: a cancelled/failed first turn must not
strand a session believing it already delivered the DEV_SYS_MESSAGE system prompt.

Before the fix, `s.system_injected = True` was set BEFORE `_stream_agent_response`
actually ran the graph. Several awaits sat in between (broadcast, persist_turn,
project/skill resolution, MCP scope entry) -- if the turn's task was cancelled or
raised anywhere in that window, the in-memory flag stayed True while the LangGraph
checkpoint never received a SystemMessage at all. Every later turn on that session
then silently ran with no DEV_SYS_MESSAGE, no tool-usage rules, no push/PR gate
description.

The fix moves the assignment to after `_stream_agent_response` returns
successfully. These tests drive `_process_ws_message` directly (same technique as
tests/development/test_ws_inflight_guard.py) with a FRESH session (system_injected
starts False, i.e. first_message=True) and assert the flag's behavior across the
success / exception / cancellation paths.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

import agents_orchestrator.development_agent.development_agent_api as dev_api
from agents_orchestrator.development_agent.config.session_state import (
    clear_session,
    get_session,
)

pytestmark = pytest.mark.asyncio


class _NullDBCtx:
    async def __aenter__(self):
        return MagicMock()

    async def __aexit__(self, exc_type, exc, tb):
        return False


def _fake_get_db_session_for_tenant(tenant_id):
    return _NullDBCtx()


def _fake_websocket() -> MagicMock:
    ws = MagicMock()
    ws.send_text = AsyncMock()
    ws.accept = AsyncMock()
    return ws


def _patch_first_message_collaborators(monkeypatch):
    """Stub out everything _process_ws_message's first_message branch calls before
    reaching _stream_agent_response, so these tests exercise the system_injected
    timing itself rather than real DB/context-broker/MCP plumbing (tenant_id=""
    already makes the MCP helpers no-op; these four are the ones that don't)."""
    monkeypatch.setattr(dev_api, "assert_agent_access_for_chat", AsyncMock(return_value=None))
    monkeypatch.setattr(dev_api, "get_db_session_for_tenant", _fake_get_db_session_for_tenant)
    monkeypatch.setattr(dev_api, "_bind_pulled_workspace", AsyncMock(return_value=""))
    monkeypatch.setattr(dev_api, "_build_dev_session_context", AsyncMock(return_value=""))
    monkeypatch.setattr(dev_api, "resolve_agent_turn", AsyncMock(return_value=("DEV SYS PROMPT", [])))
    monkeypatch.setattr(dev_api, "resolve_agent_skills", AsyncMock(return_value=[]))


@pytest.fixture
def _clean_inflight():
    dev_api._INFLIGHT_SESSIONS.clear()
    yield
    dev_api._INFLIGHT_SESSIONS.clear()


async def test_system_injected_set_true_only_after_successful_first_turn(
    monkeypatch, _clean_inflight,
):
    session_id = "first-message-success-test"
    clear_session(session_id)
    s = get_session(session_id)
    assert s.system_injected is False

    _patch_first_message_collaborators(monkeypatch)
    mock_stream = AsyncMock(return_value="final agent response")
    monkeypatch.setattr(dev_api, "_stream_agent_response", mock_stream)

    ws = _fake_websocket()
    await dev_api._process_ws_message(
        {"session_id": session_id, "messages": [{"role": "user", "content": "hello"}]},
        ws, "user-1", tenant_id="",
    )

    mock_stream.assert_called_once()
    # The state handed to the graph actually included the SystemMessage on this,
    # the session's first turn.
    state_arg = mock_stream.await_args.args[0]
    from langchain_core.messages import SystemMessage
    assert isinstance(state_arg["messages"][0], SystemMessage)

    assert s.system_injected is True

    clear_session(session_id)


async def test_system_injected_stays_false_when_first_turn_raises(
    monkeypatch, _clean_inflight,
):
    """A genuine error during the session's first turn must NOT leave the flag set --
    otherwise the next turn silently skips the system prompt entirely."""
    session_id = "first-message-exception-test"
    clear_session(session_id)
    s = get_session(session_id)
    assert s.system_injected is False

    _patch_first_message_collaborators(monkeypatch)
    monkeypatch.setattr(
        dev_api, "_stream_agent_response",
        AsyncMock(side_effect=RuntimeError("simulated graph failure")),
    )

    ws = _fake_websocket()
    # _process_ws_message swallows the exception internally -- must not raise out.
    await dev_api._process_ws_message(
        {"session_id": session_id, "messages": [{"role": "user", "content": "hello"}]},
        ws, "user-1", tenant_id="",
    )

    assert s.system_injected is False

    clear_session(session_id)


async def test_system_injected_stays_false_when_first_turn_is_cancelled(
    monkeypatch, _clean_inflight,
):
    """Mirrors the real trigger from the review: task.cancel() landing in the window
    between the old (pre-fix) early flag-set and the graph actually running. With the
    fix, since the flag is only set AFTER _stream_agent_response returns, a
    cancellation anywhere before that leaves it False."""
    session_id = "first-message-cancel-test"
    clear_session(session_id)
    s = get_session(session_id)
    assert s.system_injected is False

    _patch_first_message_collaborators(monkeypatch)

    started = asyncio.Event()

    async def _hang_forever(*args, **kwargs):
        started.set()
        await asyncio.sleep(30)  # cancelled long before this elapses

    monkeypatch.setattr(dev_api, "_stream_agent_response", _hang_forever)

    ws = _fake_websocket()
    task = asyncio.create_task(
        dev_api._process_ws_message(
            {"session_id": session_id, "messages": [{"role": "user", "content": "hello"}]},
            ws, "user-1", tenant_id="",
        )
    )
    await started.wait()
    # Cancellation lands while _stream_agent_response is in flight -- the exact
    # window the review flagged.
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert s.system_injected is False

    clear_session(session_id)
