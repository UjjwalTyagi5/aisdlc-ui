"""A message addressed to one session never reaches another.

FOUND WHILE FIXING THE DESIGN AGENT. `ConnectionManager.broadcast` fell back to
sending to EVERY active connection whenever the message's `session_id` had no
registered socket — and its own docstring said the fallback happened

    "only when session_id is absent from the message (e.g. agents_cleared, legacy
     paths)."

That is not what the code did. The `else` fired in two cases: session_id absent (as
documented), and session_id PRESENT but not registered (undocumented). The second is
a cross-session leak, and it is not hypothetical: nothing registers a socket under an
orchestrator2 run id — `ws.py` and `dispatch.py` never call `register_session` — so
EVERY orchestrator2 run took the undocumented branch. A design document generated
through the Orchestrator was streamed, token by token, onto whatever unrelated
sockets happened to be open on that process.

79 call sites reach `broadcast()`. Fixing them one at a time would have left the next
one to reintroduce it, so the fallback itself is what changed: absent `session_id`
still fans out, because `agents_cleared` and the legacy paths genuinely have no
session to address. A `session_id` that names nobody now sends to nobody.

WHY SILENCE IS THE RIGHT FAILURE. The alternative to delivering a session's payload
to strangers is not delivering it. Both lose the message for its intended reader —
the socket is not connected either way — but only one of them hands it to someone
else.

This is the twelfth instance on this branch of prose asserting a guarantee the code
does not provide. It is the first with a confidentiality consequence, which is why
the docstring is now asserted by a test rather than trusted.
"""
import json

import pytest

from config.connection_manager import ConnectionManager


class _FakeSocket:
    def __init__(self, name):
        self.name = name
        self.received = []
        self.accepted = False

    async def accept(self):
        self.accepted = True

    async def send_text(self, raw):
        self.received.append(json.loads(raw))

    def types(self):
        return [m.get("type") for m in self.received]


async def _connected(manager, *names):
    out = []
    for name in names:
        ws = _FakeSocket(name)
        await manager.connect(ws)
        out.append(ws)
    return out


@pytest.mark.asyncio
async def test_an_unregistered_session_does_not_fan_out_to_everyone():
    """THE LEAK. `alice` is registered; the message names a session nobody holds."""
    manager = ConnectionManager()
    alice, bob = await _connected(manager, "alice", "bob")
    manager.register_session(alice, "alice-session")
    manager.register_session(bob, "bob-session")

    await manager.broadcast({
        "type": "stream_chunk",
        "session_id": "9de55574-orchestrator-run",   # nobody registered this
        "content": "the confidential architecture document",
    })

    assert alice.received == [], "another session's document reached alice"
    assert bob.received == [], "another session's document reached bob"


@pytest.mark.asyncio
async def test_a_registered_session_still_receives_its_own_messages():
    """The other half. A fix that delivers nothing is not this fix."""
    manager = ConnectionManager()
    alice, bob = await _connected(manager, "alice", "bob")
    manager.register_session(alice, "alice-session")
    manager.register_session(bob, "bob-session")

    await manager.broadcast({"type": "stream_chunk", "session_id": "alice-session",
                             "content": "hers"})

    assert alice.types() == ["stream_chunk"]
    assert bob.received == [], "alice's message reached bob"


@pytest.mark.asyncio
async def test_a_message_with_no_session_still_reaches_everyone():
    """The DOCUMENTED fallback, which is legitimate and must survive: `agents_cleared`
    and the legacy paths address no session at all."""
    manager = ConnectionManager()
    alice, bob = await _connected(manager, "alice", "bob")
    manager.register_session(alice, "alice-session")

    await manager.broadcast({"type": "agents_cleared"})

    assert alice.types() == ["agents_cleared"]
    assert bob.types() == ["agents_cleared"]


@pytest.mark.asyncio
async def test_an_empty_session_id_counts_as_no_session():
    """`""` is what a caller passes when it has no session, not a session named
    empty-string. Treating it as unregistered would silence the legacy paths."""
    manager = ConnectionManager()
    alice, = await _connected(manager, "alice")
    manager.register_session(alice, "alice-session")

    await manager.broadcast({"type": "agents_cleared", "session_id": ""})
    assert alice.types() == ["agents_cleared"]


@pytest.mark.asyncio
async def test_a_session_whose_only_socket_disconnected_sends_to_nobody():
    """The live shape of the leak: a session that WAS registered and is now gone.
    Falling back here would deliver a departed user's payload to whoever is left."""
    manager = ConnectionManager()
    alice, bob = await _connected(manager, "alice", "bob")
    manager.register_session(alice, "alice-session")
    manager.register_session(bob, "bob-session")
    manager.disconnect(alice)

    await manager.broadcast({"type": "stream_chunk", "session_id": "alice-session",
                             "content": "hers"})

    assert bob.received == [], "a departed session's message reached bob"


@pytest.mark.asyncio
async def test_broadcast_to_session_is_unchanged():
    """It already never fell back. Kept honest so the two cannot drift into
    disagreeing about what addressing a session means."""
    manager = ConnectionManager()
    alice, bob = await _connected(manager, "alice", "bob")
    manager.register_session(alice, "alice-session")

    await manager.broadcast_to_session({"type": "x", "session_id": "nobody"})
    assert alice.received == [] and bob.received == []

    await manager.broadcast_to_session({"type": "x", "session_id": "alice-session"})
    assert alice.types() == ["x"] and bob.received == []


def test_the_docstring_describes_the_branch_the_code_actually_takes():
    """The docstring said the fallback applied "only when session_id is absent". The
    code also fell back when it was present-but-unregistered, and that gap is what
    made the leak invisible to everyone reading the call sites.
    """
    doc = ConnectionManager.broadcast.__doc__ or ""
    lowered = doc.lower()
    assert "absent" in lowered, "the documented fallback condition is gone"
    assert ("unregistered" in lowered or "no registered" in lowered
            or "names nobody" in lowered), (
        "the docstring still does not say what happens to a session_id nobody holds "
        "— the exact silence that hid a cross-session leak"
    )
