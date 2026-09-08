"""Stopping a turn that is already generating.

WHY CLOSING THE SOCKET IS NOT ENOUGH. The chat BFF opens a WebSocket per turn and closes
it when the browser aborts — but an agent notices that only at its next `receive_text()`,
which does not run until the turn it is in the middle of has finished. So pressing Stop
made the chat go quiet while the agent kept generating: still spending tokens, still
running tools, still writing documents that appeared a minute later. From the outside
that reads as a Stop button that does not work, and it was reported as exactly that.

THE SIGNAL HAS TO ARRIVE OUT OF BAND, because the socket the agent would read it on is
the one that is blocked. A separate request marks the session cancelled here; the agent
discovers it at the next thing it emits.

AND IT IS CHECKED AT THE ONE PLACE EVERY AGENT ALREADY GOES THROUGH.
`ConnectionManager.broadcast` has around sixty call sites across every agent in this
codebase — token deltas, tool notices, completion events. Raising `TurnCancelled` there
unwinds whatever loop is producing output, whichever agent it belongs to, without asking
nine separate stream loops to learn a new convention. That is the same reasoning
`broadcast`'s own docstring gives for fixing session routing in one place rather than at
seventy-nine call sites.

WHAT IT CANNOT DO, stated plainly: a tool call already in flight finishes. If the agent
is three seconds into pushing a work item, that push completes — this interrupts the
turn between emissions, not the middle of an HTTP request to somebody else's API. Stop
means "produce nothing further", not "undo what is done", and the transcript should not
imply otherwise.
"""
from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)


class TurnCancelled(Exception):
    """Raised inside an agent's turn when the person who asked for it pressed Stop."""


#: session_id -> when it was marked. A dict rather than a set so entries can be aged out:
#: a cancelled session whose agent never emitted again would otherwise sit here forever,
#: and the NEXT turn on that session id would be cancelled before it started.
_CANCELLED: dict[str, float] = {}

#: A cancellation older than this is stale. Generous — it only has to outlive the gap
#: between the Stop press and the agent's next emission, which is seconds.
_TTL_SECONDS = 120.0
#: Bounded so a pathological caller cannot grow this without limit.
_MAX_ENTRIES = 512


def _prune(now: float) -> None:
    for sid, marked in list(_CANCELLED.items()):
        if now - marked > _TTL_SECONDS:
            _CANCELLED.pop(sid, None)
    while len(_CANCELLED) > _MAX_ENTRIES:
        _CANCELLED.pop(next(iter(_CANCELLED)), None)


def mark_cancelled(session_id: str) -> None:
    """Record that this session's current turn should stop."""
    if not session_id:
        return
    now = time.monotonic()
    _prune(now)
    _CANCELLED[session_id] = now
    logger.info("turn cancelled for session %s", session_id)


def is_cancelled(session_id: str) -> bool:
    if not session_id:
        return False
    marked = _CANCELLED.get(session_id)
    if marked is None:
        return False
    if time.monotonic() - marked > _TTL_SECONDS:
        _CANCELLED.pop(session_id, None)
        return False
    return True


def clear(session_id: str) -> None:
    """Forget a cancellation — called when a session starts a NEW turn.

    Without this, stopping one turn would cancel every later turn on the same
    conversation, which is a far worse bug than the one this module fixes.
    """
    if session_id:
        _CANCELLED.pop(session_id, None)
