"""Tests for Task 4 of the Development Agent chat overhaul: the per-session
in-flight guard (_INFLIGHT_SESSIONS) and task-tracked cancellation on disconnect.

Why this exists: if a frontend WS connection drops mid-turn (reconnect, network
blip, or the frontend's own idle-fallback) and a new connection opens for the
SAME session_id, the ORIGINAL in-flight _process_ws_message call used to keep
running unsupervised -- nothing cancelled it. Both calls read/write the same
DevSessionState and drive the same LangGraph checkpoint (thread_id=session_id),
producing duplicated/overlapping replies. This is fixed two ways:

  1. `_INFLIGHT_SESSIONS`: a module-level set checked at the top of
     `_process_ws_message` -- a second turn for a session already in flight is
     rejected with a "still processing" notice instead of running the graph.
  2. `websocket_endpoint` now dispatches `_process_ws_message` via
     `asyncio.create_task` (tracked per-connection) and cancels any tracked
     task when the connection's disconnect/error branches fire, so an orphaned
     turn is actively cancelled rather than left to run.

These tests cover (a)/(b)/(c) from the task-4 brief directly against
`_process_ws_message`, plus a direct exercise of `websocket_endpoint`'s
disconnect branch to confirm it cancels the tracked task.
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import WebSocketDisconnect

import agents_orchestrator.development_agent.development_agent_api as dev_api
from agents_orchestrator.development_agent.config.session_state import (
    clear_session,
    get_session,
)

pytestmark = pytest.mark.asyncio


class _NullDBCtx:
    """Stand-in for get_db_session_for_tenant's async context manager -- no real
    DB connection, just enough shape for `async with ... as db:` to work."""

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


@pytest.fixture
def _clean_inflight():
    """Guarantee _INFLIGHT_SESSIONS starts and ends empty for every test in this
    module, regardless of pass/fail, so tests never bleed state into each other."""
    dev_api._INFLIGHT_SESSIONS.clear()
    yield
    dev_api._INFLIGHT_SESSIONS.clear()


# -- (a) second turn for an already in-flight session is rejected -------------

async def test_second_message_for_inflight_session_is_rejected_without_running_graph(
    monkeypatch, _clean_inflight,
):
    session_id = "ws-inflight-guard-reject-test"
    clear_session(session_id)
    dev_api._INFLIGHT_SESSIONS.add(session_id)

    mock_stream = AsyncMock()
    monkeypatch.setattr(dev_api, "_stream_agent_response", mock_stream)

    ws = _fake_websocket()
    sent: list[dict] = []

    async def _capture(message: str, websocket):
        sent.append(json.loads(message))

    monkeypatch.setattr(dev_api.manager, "send_personal_message", AsyncMock(side_effect=_capture))

    await dev_api._process_ws_message(
        {"session_id": session_id, "messages": [{"role": "user", "content": "second message"}]},
        ws, "user-1", tenant_id="",
    )

    mock_stream.assert_not_called()

    types = [m.get("type") for m in sent]
    assert types == ["stream_chunk", "stream_end"]
    assert "Still processing" in sent[0]["content"]
    assert sent[0]["session_id"] == session_id
    assert sent[1]["session_id"] == session_id

    # The guard rejected without ever removing/touching the marker it didn't set --
    # the session is still (correctly) marked in-flight by whatever put it there.
    assert session_id in dev_api._INFLIGHT_SESSIONS

    clear_session(session_id)


# -- (b) cleared after normal completion ---------------------------------------

async def test_inflight_marker_cleared_after_normal_completion(monkeypatch, _clean_inflight):
    session_id = "ws-inflight-guard-success-test"
    clear_session(session_id)
    s = get_session(session_id)
    s.system_injected = True  # skip the first-message system-prompt branch

    monkeypatch.setattr(dev_api, "assert_agent_access_for_chat", AsyncMock(return_value=None))
    monkeypatch.setattr(dev_api, "get_db_session_for_tenant", _fake_get_db_session_for_tenant)
    mock_stream = AsyncMock(return_value="final agent response")
    monkeypatch.setattr(dev_api, "_stream_agent_response", mock_stream)

    ws = _fake_websocket()

    assert session_id not in dev_api._INFLIGHT_SESSIONS
    await dev_api._process_ws_message(
        {"session_id": session_id, "messages": [{"role": "user", "content": "hello"}]},
        ws, "user-1", tenant_id="",
    )

    mock_stream.assert_called_once()
    assert session_id not in dev_api._INFLIGHT_SESSIONS

    clear_session(session_id)


# -- (c) cleared after an exception during processing ---------------------------

async def test_inflight_marker_cleared_after_exception_during_processing(
    monkeypatch, _clean_inflight,
):
    session_id = "ws-inflight-guard-exception-test"
    clear_session(session_id)
    s = get_session(session_id)
    s.system_injected = True

    monkeypatch.setattr(dev_api, "assert_agent_access_for_chat", AsyncMock(return_value=None))
    monkeypatch.setattr(dev_api, "get_db_session_for_tenant", _fake_get_db_session_for_tenant)
    monkeypatch.setattr(
        dev_api, "_stream_agent_response",
        AsyncMock(side_effect=RuntimeError("simulated graph failure")),
    )

    ws = _fake_websocket()

    assert session_id not in dev_api._INFLIGHT_SESSIONS
    # _process_ws_message's own except Exception clause swallows the error and
    # sends an error message -- it must not raise out to the caller.
    await dev_api._process_ws_message(
        {"session_id": session_id, "messages": [{"role": "user", "content": "hello"}]},
        ws, "user-1", tenant_id="",
    )

    assert session_id not in dev_api._INFLIGHT_SESSIONS

    clear_session(session_id)


# -- CancelledError still runs the in-flight cleanup (direct verification) -----

async def test_inflight_marker_cleared_on_task_cancellation(monkeypatch, _clean_inflight):
    session_id = "ws-inflight-guard-cancel-test"
    clear_session(session_id)
    s = get_session(session_id)
    s.system_injected = True

    monkeypatch.setattr(dev_api, "assert_agent_access_for_chat", AsyncMock(return_value=None))
    monkeypatch.setattr(dev_api, "get_db_session_for_tenant", _fake_get_db_session_for_tenant)

    started = asyncio.Event()

    async def _hang_forever(*args, **kwargs):
        started.set()
        await asyncio.sleep(30)  # will be cancelled long before this elapses

    monkeypatch.setattr(dev_api, "_stream_agent_response", _hang_forever)

    ws = _fake_websocket()
    task = asyncio.create_task(
        dev_api._process_ws_message(
            {"session_id": session_id, "messages": [{"role": "user", "content": "hello"}]},
            ws, "user-1", tenant_id="",
        )
    )
    await started.wait()
    assert session_id in dev_api._INFLIGHT_SESSIONS

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert session_id not in dev_api._INFLIGHT_SESSIONS

    clear_session(session_id)


# -- Step 2: websocket_endpoint cancels the tracked task on disconnect ---------

async def test_websocket_endpoint_cancels_inflight_task_on_disconnect(monkeypatch):
    """Drives websocket_endpoint directly: first receive_text() returns a
    user_message_with_files payload (dispatched via asyncio.create_task per
    Step 2), the second receive_text() raises WebSocketDisconnect. Asserts the
    tracked task for the dropped connection is actually cancelled -- this is the
    mechanism that removes the orphaned-turn side of the race at its source."""
    session_id = "ws-endpoint-disconnect-cancel-test"
    clear_session(session_id)

    monkeypatch.setattr(dev_api, "_redeem_ws_ticket", AsyncMock(return_value={"user_id": "user-1", "tenant_id": ""}))
    monkeypatch.setattr(dev_api.manager, "connect", AsyncMock())
    monkeypatch.setattr(dev_api.manager, "register_session", MagicMock())
    monkeypatch.setattr(dev_api, "set_websocket_context", MagicMock())
    monkeypatch.setattr(dev_api, "set_session_id", MagicMock())
    monkeypatch.setattr(dev_api, "set_user_id", MagicMock())
    monkeypatch.setattr(dev_api, "set_provider_kind", MagicMock())

    disconnect_calls: list = []
    monkeypatch.setattr(dev_api.manager, "disconnect", MagicMock(side_effect=disconnect_calls.append))

    started = asyncio.Event()
    was_cancelled = asyncio.Event()

    async def _fake_process_ws_message(message_data, websocket, user_id, tenant_id=""):
        started.set()
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            was_cancelled.set()
            raise

    monkeypatch.setattr(dev_api, "_process_ws_message", _fake_process_ws_message)

    ws = _fake_websocket()
    ws.query_params = {"ticket": "fake-ticket"}

    user_msg = json.dumps({
        "type": "user_message_with_files",
        "session_id": session_id,
        "messages": [{"role": "user", "content": "hi"}],
    })

    call_count = {"n": 0}

    async def _receive_text():
        call_count["n"] += 1
        if call_count["n"] == 1:
            return user_msg
        # Real Starlette I/O always yields to the loop before the next message/
        # disconnect arrives, which is what lets the task created for message 1
        # actually start running before message 2 (the disconnect) is handled.
        # Force that same yield here so the fake mirrors real scheduling.
        await asyncio.sleep(0)
        raise WebSocketDisconnect()

    ws.receive_text = AsyncMock(side_effect=_receive_text)

    await dev_api.websocket_endpoint(ws)

    # websocket_endpoint's disconnect branch calls task.cancel() fire-and-forget
    # (per the brief: no awaiting cancellation with a timeout that could hang the
    # handler) -- give the event loop one chance to actually deliver the
    # CancelledError into the task before asserting on it.
    try:
        await asyncio.wait_for(was_cancelled.wait(), timeout=1.0)
    except asyncio.TimeoutError:
        pass

    # The in-flight task was created, started, and then actively cancelled by the
    # disconnect branch -- not left running unsupervised against session_id.
    assert started.is_set()
    assert was_cancelled.is_set()
    assert len(disconnect_calls) == 1

    clear_session(session_id)
