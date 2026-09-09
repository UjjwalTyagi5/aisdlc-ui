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


# -- one turn's output must not land in another turn's bubble -----------------


@pytest.mark.asyncio
async def test_an_answer_reaches_only_the_turn_that_asked():
    """THE CROSS-TURN BLEED, reported as "the answer for the second message showed up
    on the first".

    A conversation can have more than one socket open: the BFF opens one per turn, and a
    turn whose agent has not yet noticed it ended leaves its socket registered. Every one
    of them received every message, so an answer arrived on a stream that had asked a
    different question and was appended to that bubble.

    Sessions still decide who may hear it; the turn decides which of that session's
    sockets asked.
    """
    from config.ws_helper import set_turn_id

    mgr = ConnectionManager()
    first, second = _Socket(), _Socket()
    mgr.active_connections.extend([first, second])   # type: ignore[arg-type]

    mgr.register_session(first, "s-1")               # type: ignore[arg-type]
    # Turn two arrives on its own socket and becomes the turn this task is serving.
    mgr.register_session(second, "s-1")              # type: ignore[arg-type]

    await mgr.broadcast({"session_id": "s-1", "type": "token", "text": "answer to two"})

    assert len(second.sent) == 1, "the turn that asked must get its answer"
    assert first.sent == [], "the earlier turn asked a different question"

    set_turn_id(None)


@pytest.mark.asyncio
async def test_a_message_with_no_turn_still_reaches_the_whole_session():
    """MULTI-SOCKET SESSIONS SURVIVE, and that is deliberate — a second browser tab on
    the same conversation is a supported thing, and `broadcast_to_session` has a test
    asserting exactly that. Only output produced INSIDE a turn is narrowed; anything
    emitted with no turn in context still fans out."""
    from config.ws_helper import set_turn_id

    mgr = ConnectionManager()
    a, b = _Socket(), _Socket()
    mgr.active_connections.extend([a, b])            # type: ignore[arg-type]
    mgr.register_session(a, "s-1")                   # type: ignore[arg-type]
    mgr.register_session(b, "s-1")                   # type: ignore[arg-type]

    set_turn_id(None)  # e.g. a background emitter, not serving a turn
    await mgr.broadcast({"session_id": "s-1", "type": "notice", "text": "for everyone"})

    assert len(a.sent) == 1 and len(b.sent) == 1


@pytest.mark.asyncio
async def test_a_turn_whose_socket_has_gone_falls_back_to_the_session():
    """Losing output somebody may still be waiting for is worse than showing it on a
    second tab, so a vanished socket does not mean silence."""
    from config.ws_helper import set_turn_id

    mgr = ConnectionManager()
    gone, other = _Socket(), _Socket()
    mgr.active_connections.extend([gone, other])     # type: ignore[arg-type]
    mgr.register_session(gone, "s-1")                # type: ignore[arg-type]
    mgr.register_session(other, "s-1")               # type: ignore[arg-type]

    # The turn being served is the FIRST socket, which has since disconnected.
    turn = next(t for t, ws in mgr._turn_socket.items() if ws is gone)
    mgr.disconnect(gone)                             # type: ignore[arg-type]
    set_turn_id(turn)

    await mgr.broadcast({"session_id": "s-1", "type": "token", "text": "late answer"})

    assert len(other.sent) == 1
    set_turn_id(None)
