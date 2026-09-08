"""Stop reaches the agent, not just the screen.

THE BUG THIS FIXES, reported as "the stop button isn't working". Pressing Stop aborted
the browser's stream and closed the BFF's WebSocket — and the agent carried on. It
notices a closed connection only at its next `receive_text()`, which does not run until
the turn it is inside has finished, so it kept generating, kept spending tokens, and kept
running tools whose output turned up a minute later. From the outside that is
indistinguishable from a button wired to nothing.

TWO PROPERTIES MATTER, and the second is the one that would ruin the feature.

  · A cancelled session must actually interrupt the agent. It does, at the next thing
    the agent emits, because every agent's output funnels through
    `ConnectionManager.broadcast`.
  · A cancellation must NOT outlive its turn. Stopping one turn and then asking a new
    question on the same conversation has to work — a Stop that quietly breaks the rest
    of the chat is a worse bug than the one it fixes.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import turn_cancellation as tc  # noqa: E402
from config.connection_manager import ConnectionManager  # noqa: E402


class _Socket:
    def __init__(self):
        self.sent: list[str] = []

    async def send_text(self, text: str) -> None:
        self.sent.append(text)


@pytest.fixture(autouse=True)
def _clean():
    tc._CANCELLED.clear()
    yield
    tc._CANCELLED.clear()


@pytest.mark.asyncio
async def test_a_cancelled_session_interrupts_the_agents_next_emission():
    """THE HEADLINE. The agent is inside a turn and cannot read its socket; this is how
    it finds out."""
    mgr = ConnectionManager()
    ws = _Socket()
    mgr.active_connections.append(ws)          # type: ignore[arg-type]
    mgr.register_session(ws, "s-1")            # type: ignore[arg-type]

    tc.mark_cancelled("s-1")

    with pytest.raises(tc.TurnCancelled):
        await mgr.broadcast({"session_id": "s-1", "type": "token", "text": "more output"})
    assert ws.sent == [], "a cancelled turn must not keep streaming"


@pytest.mark.asyncio
async def test_an_uncancelled_session_streams_normally():
    """NON-VACUITY: the check must not break the ordinary path that every agent uses."""
    mgr = ConnectionManager()
    ws = _Socket()
    mgr.active_connections.append(ws)          # type: ignore[arg-type]
    mgr.register_session(ws, "s-1")            # type: ignore[arg-type]

    await mgr.broadcast({"session_id": "s-1", "type": "token", "text": "hello"})

    assert len(ws.sent) == 1


@pytest.mark.asyncio
async def test_another_sessions_turn_is_untouched():
    """Cancelling is per conversation. One person stopping their turn must not
    interrupt somebody else's, which the shared choke point makes easy to get wrong."""
    mgr = ConnectionManager()
    mine, theirs = _Socket(), _Socket()
    mgr.active_connections.extend([mine, theirs])   # type: ignore[arg-type]
    mgr.register_session(mine, "s-1")               # type: ignore[arg-type]
    mgr.register_session(theirs, "s-2")             # type: ignore[arg-type]

    tc.mark_cancelled("s-1")

    await mgr.broadcast({"session_id": "s-2", "type": "token", "text": "still going"})
    assert len(theirs.sent) == 1


@pytest.mark.asyncio
async def test_a_new_turn_on_the_same_session_is_not_cancelled():
    """THE ONE THAT WOULD RUIN IT. The BFF opens a socket per turn, so registering one
    means a new turn — and a stale cancellation left in place would abort every later
    question on that conversation until the TTL expired. The chat would simply stop
    answering, with Stop as the cause and nothing on screen to say so."""
    mgr = ConnectionManager()
    first = _Socket()
    mgr.active_connections.append(first)       # type: ignore[arg-type]
    mgr.register_session(first, "s-1")         # type: ignore[arg-type]
    tc.mark_cancelled("s-1")
    assert tc.is_cancelled("s-1") is True

    # The next turn opens its own socket.
    second = _Socket()
    mgr.active_connections.append(second)      # type: ignore[arg-type]
    mgr.register_session(second, "s-1")        # type: ignore[arg-type]

    assert tc.is_cancelled("s-1") is False
    await mgr.broadcast({"session_id": "s-1", "type": "token", "text": "answer"})
    assert len(second.sent) == 1


def test_a_cancellation_ages_out():
    """A session whose agent never emitted again would otherwise sit cancelled forever,
    and the entry would grow the registry without bound."""
    tc.mark_cancelled("s-old")
    tc._CANCELLED["s-old"] = tc._CANCELLED["s-old"] - (tc._TTL_SECONDS + 1)

    assert tc.is_cancelled("s-old") is False


def test_an_empty_session_id_cancels_nothing():
    """`broadcast` falls back to every connection when a message names no session.
    Treating "" as cancellable would let one stray call silence the whole process."""
    tc.mark_cancelled("")

    assert tc.is_cancelled("") is False
    assert tc._CANCELLED == {}
