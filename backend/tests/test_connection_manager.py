"""Tests for ConnectionManager.broadcast_to_session (final-review.md C1 fix).

Root cause being fixed: ConnectionManager.broadcast() falls back to fanning a
message out to EVERY active connection whenever message['session_id'] is present
but not registered under _session_connections. file_diff payloads (full
before/after source code) are exactly the class of message that hit this fallback
on the Copilot/pipeline surface, which never calls manager.register_session --
leaking one tenant's source code to every other open WebSocket on the process.

broadcast_to_session is the narrower method: it sends ONLY to connections
registered for the given session_id, and does nothing at all -- no fallback -- when
that session has no registered connections.

UPDATE: broadcast() itself has since been narrowed the same way, because leaving its
fallback in place left the leak described above live for all 79 of its call sites --
and orchestrator2, which never calls register_session, took that branch on every
single run. A message naming a session nobody holds now goes to nobody. A message
naming no session at all still goes to everyone, which is what agents_cleared and the
legacy paths actually mean.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from config.connection_manager import ConnectionManager

pytestmark = pytest.mark.asyncio


def _fake_ws():
    ws = AsyncMock()
    ws.send_text = AsyncMock()
    return ws


async def test_broadcast_to_session_sends_only_to_registered_connections():
    manager = ConnectionManager()

    ws_a1 = _fake_ws()
    ws_a2 = _fake_ws()
    ws_b1 = _fake_ws()
    unregistered_ws = _fake_ws()

    # Simulate connect() populating active_connections for all four sockets.
    manager.active_connections.extend([ws_a1, ws_a2, ws_b1, unregistered_ws])

    manager.register_session(ws_a1, "session-a")
    manager.register_session(ws_a2, "session-a")
    manager.register_session(ws_b1, "session-b")
    # unregistered_ws is connected (in active_connections) but never registered
    # under any session -- mirrors a socket on a surface that never calls
    # register_session (e.g. Copilot/pipeline), or a socket for an unrelated turn.

    await manager.broadcast_to_session({"type": "file_diff", "session_id": "session-a", "path": "x"})

    ws_a1.send_text.assert_awaited_once()
    ws_a2.send_text.assert_awaited_once()
    ws_b1.send_text.assert_not_called()
    unregistered_ws.send_text.assert_not_called()

    sent_payload = json.loads(ws_a1.send_text.await_args.args[0])
    assert sent_payload["session_id"] == "session-a"


async def test_broadcast_to_session_with_no_registered_connections_sends_to_nobody():
    """The actual security property: an unregistered (but non-empty) session_id
    must NOT fall back to active_connections, unlike broadcast()."""
    manager = ConnectionManager()

    other_tenant_ws_1 = _fake_ws()
    other_tenant_ws_2 = _fake_ws()
    manager.active_connections.extend([other_tenant_ws_1, other_tenant_ws_2])
    # Neither socket is registered under any session_id -- active_connections is
    # non-empty, but _session_connections has no entry for "orphaned-session" at all.

    await manager.broadcast_to_session({
        "type": "file_diff",
        "session_id": "orphaned-session",
        "path": "src/secret.py",
        "original": "",
        "modified": "TOP_SECRET_TENANT_CODE",
    })

    other_tenant_ws_1.send_text.assert_not_called()
    other_tenant_ws_2.send_text.assert_not_called()


async def test_broadcast_to_session_with_missing_session_id_sends_to_nobody():
    manager = ConnectionManager()
    ws = _fake_ws()
    manager.active_connections.append(ws)

    await manager.broadcast_to_session({"type": "file_diff", "path": "x"})

    ws.send_text.assert_not_called()


async def test_broadcast_no_longer_falls_back_when_the_session_names_nobody():
    """REVERSED, deliberately. This test used to assert the opposite.

    The C1 fix above added `broadcast_to_session` and left `broadcast`'s fallback
    alone so "broadcast_log and other pre-existing callers keep working exactly as
    before". That left the leak this module's own docstring describes — one tenant's
    payload reaching every open socket — live for all 79 `broadcast()` call sites.
    The decision was "do not break existing callers", not "the leak is acceptable".

    What settled it: orchestrator2 never calls `register_session` at all, so EVERY
    Orchestrator run takes the fallback. And in that case the fallback cannot be
    doing its job — the intended reader is not on any of those sockets, so the only
    thing it achieves is delivery to strangers. A fallback that never reaches its
    audience is not compatibility, it is only the leak.

    The documented fallback survives: a message with NO session_id still reaches
    everyone, because `agents_cleared` and the legacy paths genuinely address no one
    session. See tests/test_broadcast_never_leaks_across_sessions.py.
    """
    manager = ConnectionManager()
    ws1 = _fake_ws()
    ws2 = _fake_ws()
    manager.active_connections.extend([ws1, ws2])

    await manager.broadcast({"type": "activity_update", "session_id": "unregistered-session"})

    ws1.send_text.assert_not_awaited()
    ws2.send_text.assert_not_awaited()


async def test_broadcast_without_a_session_id_still_reaches_everyone():
    """The half of the fallback that was always legitimate, and must not be lost
    along with the half that leaked."""
    manager = ConnectionManager()
    ws1 = _fake_ws()
    ws2 = _fake_ws()
    manager.active_connections.extend([ws1, ws2])

    await manager.broadcast({"type": "agents_cleared"})

    ws1.send_text.assert_awaited_once()
    ws2.send_text.assert_awaited_once()
