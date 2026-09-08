"""The socket hands the user's attachments to the agent that answers the turn.

`test_attachment_context.py` proves the BLOCK is built correctly. This file proves it
is actually delivered — which is the half that decides whether the feature works, and
the half that a unit test of the renderer cannot see.

FAKES LIE, so the ingestion here is real. Only the things that need a database or a
network are stubbed (ticket redemption, role and run resolution, and `run_agent`
itself); `attachment_context`, `attachment_store` and the real text extractor all run
for real against a temp directory. Two defects on this branch reached a green suite
because every test used a dict or a monkeypatched function in place of the real object,
so a test that stubbed `attachment_context` here would be asserting that this file's
own stub returns what this file's own stub was told to return.

What is pinned:

  * the attachment block reaches `run_agent(context=...)` — the ONE argument the agent's
    prompt is assembled from (`dispatch.run_agent` puts it in a second SystemMessage);
  * it does not displace what the run already holds — the two blocks coexist;
  * the uploader is the TICKET's user, never a value off the frame;
  * a listing that fails REFUSES the turn instead of running an agent that will report
    the user attached nothing.
"""
import json

import pytest

from shared.services import attachment_store

_RUN = "11111111-1111-1111-1111-111111111111"
#: The authenticated user in `_patch_auth` below — the ticket's, and the only key
#: `attachment_store` should ever be read under.
_TICKET_USER = "u1"


class _FakeWebSocket:
    def __init__(self, *, params=None, inbound=None):
        self.query_params = dict(params or {})
        self._inbound = list(inbound or [])
        self.accepted = False
        self.closed = None
        self.sent = []

    async def accept(self):
        self.accepted = True

    async def close(self, code=1000, reason=""):
        self.closed = (code, reason)

    async def receive_text(self):
        from fastapi import WebSocketDisconnect
        if not self._inbound:
            raise WebSocketDisconnect()
        return self._inbound.pop(0)

    async def send_text(self, raw):
        self.sent.append(json.loads(raw))


@pytest.fixture
def ws():
    from agents_orchestrator.orchestrator2 import ws as ws_module
    return ws_module


@pytest.fixture
def store(tmp_path, monkeypatch):
    """The real attachment store, rooted in a temp directory."""
    monkeypatch.setattr(attachment_store, "_FILES_ROOT", tmp_path / "files")
    return attachment_store


def _patch_auth(monkeypatch, ws, *, project_id="proj-A"):
    async def _fake_redeem(ticket):
        return {"user_id": _TICKET_USER, "tenant_id": "t1"}

    async def _fake_role(user_id, tenant_id):
        return "project_admin"

    async def _owned(run_id, tenant_id):
        return ws.RunSelection(model_id="m1", offering_id="o1", project_id=project_id)

    async def _permissions(user_id, tenant_id):
        return ["agent:use"]

    async def _tier(project_id, tenant_id, *, user_id, permissions):
        return "project"

    monkeypatch.setattr(ws, "_redeem_ws_ticket", _fake_redeem)
    monkeypatch.setattr(ws, "_resolve_platform_role", _fake_role)
    monkeypatch.setattr(ws, "_resolve_run", _owned)
    monkeypatch.setattr(ws, "_resolve_permissions", _permissions)
    monkeypatch.setattr(ws, "_project_admin_tier_for_run", _tier)


def _record_run_agent(monkeypatch, ws):
    calls = []

    async def _fake_run_agent(agent_id, **kwargs):
        calls.append((agent_id, kwargs))
        yield {"type": "stream_end"}

    monkeypatch.setattr(ws, "run_agent", _fake_run_agent)
    return calls


def _handoff(monkeypatch, ws, text=""):
    async def _fake(run_id, tenant_id, target_agent):
        return text

    monkeypatch.setattr(ws, "handoff_context", _fake)


def _route_to(monkeypatch, ws, agent_id="requirements"):
    class _Decision:
        def __init__(self):
            self.agent_id = agent_id
            self.reason = "because"
            self.direct_reply = None

    async def _fake_route(text, **kwargs):
        return _Decision()

    monkeypatch.setattr(ws, "route", _fake_route)


async def _serve(ws, frames, **params):
    socket = _FakeWebSocket(params={"ticket": "tkt", **params}, inbound=frames)
    await ws.orchestrator2_ws(socket)
    return socket


def _frame(**kwargs):
    base = {"type": "user_message", "text": "write the stories", "run_id": _RUN}
    base.update(kwargs)
    return json.dumps(base)


# ── the delivery ────────────────────────────────────────────────────────────


async def test_the_socket_hands_the_users_attachment_to_the_agent(
    ws, store, monkeypatch
):
    """The whole feature, end to end through the real store and the real extractor.

    `context` is the one argument an agent's prompt is assembled from — `run_agent`
    puts it in a second SystemMessage — so a file whose text is not in there is a file
    the agent never saw, however convincing the chip in the composer looked.
    """
    store.save_attachment(
        _TICKET_USER, _RUN, "brd.md", b"# BRD\n\nCheckout must support Apple Pay.",
    )
    _patch_auth(monkeypatch, ws)
    _handoff(monkeypatch, ws, "")
    _route_to(monkeypatch, ws)
    calls = _record_run_agent(monkeypatch, ws)

    await _serve(ws, [_frame()])

    assert calls, "the agent must have been dispatched"
    context = calls[0][1]["context"]
    assert "brd.md" in context
    assert "Apple Pay" in context, (
        "the attachment's CONTENT must reach the turn, not just its name"
    )


async def test_the_attachment_block_does_not_displace_the_runs_own_work(
    ws, store, monkeypatch
):
    """Attachments are a THIRD source, not a replacement for the other two.

    `handoff_context` carries the run's deliverables and its conversation. An
    attachment block that overwrote them would trade one silent context loss for
    another.
    """
    store.save_attachment(_TICKET_USER, _RUN, "brd.md", b"Apple Pay is required.")
    _patch_auth(monkeypatch, ws)
    _handoff(monkeypatch, ws, "--- WORK ALREADY ON THIS RUN ---\nthe existing PRD\n")
    _route_to(monkeypatch, ws)
    calls = _record_run_agent(monkeypatch, ws)

    await _serve(ws, [_frame()])

    context = calls[0][1]["context"]
    assert "Apple Pay" in context, "the attachment must survive"
    assert "the existing PRD" in context, "the run's own work must survive"


async def test_a_run_with_no_attachments_carries_the_handoff_context_unchanged(
    ws, store, monkeypatch
):
    """Nothing attached must cost nothing — not an empty heading, not a stray blank
    block that an agent reads as a file it was not shown."""
    _patch_auth(monkeypatch, ws)
    _handoff(monkeypatch, ws, "the run's own work")
    _route_to(monkeypatch, ws)
    calls = _record_run_agent(monkeypatch, ws)

    await _serve(ws, [_frame()])

    assert calls[0][1]["context"] == "the run's own work"


async def test_the_uploader_is_the_ticket_user_never_the_frame(
    ws, store, monkeypatch
):
    """`user_id` on the wire is ignored, as it is everywhere else on this socket.

    The store is keyed by uploader. Honouring a frame-supplied user would let a caller
    read another person's uploaded documents into their own agent's prompt — the same
    class of borrow `_resolve_run` refuses for `project_id`.
    """
    store.save_attachment(_TICKET_USER, _RUN, "mine.md", b"the ticket user's document")
    store.save_attachment("someone-else", _RUN, "theirs.md", b"another person's secret")
    _patch_auth(monkeypatch, ws)
    _handoff(monkeypatch, ws, "")
    _route_to(monkeypatch, ws)
    calls = _record_run_agent(monkeypatch, ws)

    await _serve(ws, [_frame(user_id="someone-else")])

    context = calls[0][1]["context"]
    assert "the ticket user's document" in context
    assert "another person's secret" not in context, (
        "a frame must not be able to name whose attachments are read"
    )


# ── the failure posture ─────────────────────────────────────────────────────


async def test_a_failed_attachment_read_refuses_the_turn(ws, store, monkeypatch):
    """The posture `handoff_context` already takes on this socket, kept for the files.

    Running the agent anyway would have it tell the user "you did not attach anything"
    about a document they can see in the composer, and the user would read that answer
    as informed. A visible refusal is recoverable; a confident wrong answer is not.
    """
    from agents_orchestrator.orchestrator2 import attachments as att

    async def _unavailable(run_id, *, user_id):
        raise att.AttachmentsUnavailableError("disk is on fire")

    _patch_auth(monkeypatch, ws)
    _handoff(monkeypatch, ws, "")
    _route_to(monkeypatch, ws)
    calls = _record_run_agent(monkeypatch, ws)
    monkeypatch.setattr(ws, "attachment_context", _unavailable)

    socket = await _serve(ws, [_frame()])

    assert not calls, "no agent may run on a turn whose attachments could not be read"
    types = [e["type"] for e in socket.sent]
    assert "error" in types, "the refusal must be visible to the user"
    assert types[-1] == "stream_end", "a failed turn still has to hand the composer back"
