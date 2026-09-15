"""The socket hands the project's approved documents to BOTH readers on every turn.

`test_project_documents_context.py` proves the BLOCK is built correctly and
`test_router_project_documents.py` proves the router's prompt uses it. This file proves
it is actually DELIVERED — to `route(documents=...)`, which is what lets the Orchestrator
answer "are there any artifacts in this project?" itself, and to
`run_agent(context=...)`, which is what lets the chosen agent know which id to
`read_document`.

Same shape as `test_attachments_reach_the_turn.py`: only what needs a database or a
network is stubbed. The block builder is stubbed too, because it is the thing under test
in its own file and needs a database here; what is pinned is the WIRING.

What is pinned:

  * the block reaches `route(documents=...)`;
  * the block reaches `run_agent(context=...)` and displaces neither the run's own work
    nor the user's attachments;
  * it is read for the RUN's project, from the verified `runs` row, never from the frame;
  * a project with nothing approved costs nothing — `documents=""`, context unchanged;
  * a read that fails REFUSES the turn instead of running an agent that will report the
    project holds nothing.
"""
import json

import pytest

from shared.services import attachment_store

_RUN = "11111111-1111-1111-1111-111111111111"
_TICKET_USER = "u1"
_PROJECT = "44444444-4444-4444-4444-444444444444"

_BLOCK = (
    "--- APPROVED DOCUMENTS IN THIS PROJECT ---\n"
    "### Requirements\n"
    "- **QuickLink_BRD_v1.docx** — id `942db0a8-8920-4864-9274-fa6cef071039`\n"
    "--- END APPROVED DOCUMENTS IN THIS PROJECT ---\n"
)


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
    monkeypatch.setattr(attachment_store, "_FILES_ROOT", tmp_path / "files")
    return attachment_store


def _patch_auth(monkeypatch, ws, *, project_id=_PROJECT):
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


def _documents(monkeypatch, ws, block):
    seen = []

    async def _fake(project_id, tenant_id):
        seen.append((project_id, tenant_id))
        return block

    monkeypatch.setattr(ws, "approved_documents_context", _fake)
    return seen


def _record_route(monkeypatch, ws, *, agent_id="requirements", direct_reply=None):
    calls = []

    class _Decision:
        def __init__(self):
            self.agent_id = agent_id
            self.reason = "because"
            self.direct_reply = direct_reply

    async def _fake_route(text, **kwargs):
        calls.append(kwargs)
        return _Decision()

    monkeypatch.setattr(ws, "route", _fake_route)
    return calls


async def _serve(ws, frames, **params):
    socket = _FakeWebSocket(params={"ticket": "tkt", **params}, inbound=frames)
    await ws.orchestrator2_ws(socket)
    return socket


def _frame(**kwargs):
    base = {"type": "user_message", "text": "are there any artifacts?", "run_id": _RUN}
    base.update(kwargs)
    return json.dumps(base)


# ── the delivery ────────────────────────────────────────────────────────────


async def test_the_router_is_told_what_the_project_has_approved(ws, store, monkeypatch):
    """The direct-reply path is where "fresh or empty project" came from, and `route`
    is the only thing that builds that prompt."""
    _patch_auth(monkeypatch, ws)
    _handoff(monkeypatch, ws, "")
    _documents(monkeypatch, ws, _BLOCK)
    routes = _record_route(
        monkeypatch, ws, agent_id=None, direct_reply="There is one approved document.",
    )
    _record_run_agent(monkeypatch, ws)

    socket = await _serve(ws, [_frame()])

    assert routes, "the router must have been asked"
    assert routes[0]["documents"] == _BLOCK
    chunks = [e for e in socket.sent if e.get("type") == "stream_chunk"]
    assert chunks and "one approved document" in chunks[0]["content"]


async def test_the_chosen_agent_is_told_too(ws, store, monkeypatch):
    """The agent holds `read_document`; the block is what tells it the id to read."""
    _patch_auth(monkeypatch, ws)
    _handoff(monkeypatch, ws, "")
    _documents(monkeypatch, ws, _BLOCK)
    _record_route(monkeypatch, ws)
    calls = _record_run_agent(monkeypatch, ws)

    await _serve(ws, [_frame(text="give me a PDF of the BRD")])

    assert calls, "the agent must have been dispatched"
    context = calls[0][1]["context"]
    assert "QuickLink_BRD_v1.docx" in context
    assert "942db0a8-8920-4864-9274-fa6cef071039" in context


async def test_the_documents_displace_neither_the_runs_work_nor_the_attachments(
    ws, store, monkeypatch
):
    """A FOURTH source beside deliverables, transcript and attachments — never a
    replacement for any of them."""
    store.save_attachment(_TICKET_USER, _RUN, "brd.md", b"Apple Pay is required.")
    _patch_auth(monkeypatch, ws)
    _handoff(monkeypatch, ws, "--- WORK ALREADY ON THIS RUN ---\nthe existing PRD\n")
    _documents(monkeypatch, ws, _BLOCK)
    _record_route(monkeypatch, ws)
    calls = _record_run_agent(monkeypatch, ws)

    await _serve(ws, [_frame()])

    context = calls[0][1]["context"]
    assert "Apple Pay" in context, "the attachment must survive"
    assert "the existing PRD" in context, "the run's own work must survive"
    assert "QuickLink_BRD_v1.docx" in context, "the approved documents must survive"


async def test_the_documents_are_read_for_the_runs_project_not_the_frames(
    ws, store, monkeypatch
):
    """`project_id` on the wire is ignored, as it is everywhere else on this socket. The
    verified `runs` row decides whose record is listed."""
    _patch_auth(monkeypatch, ws, project_id=_PROJECT)
    _handoff(monkeypatch, ws, "")
    seen = _documents(monkeypatch, ws, "")
    _record_route(monkeypatch, ws)
    _record_run_agent(monkeypatch, ws)

    await _serve(ws, [_frame(project_id="someone-elses-project")])

    assert seen == [(_PROJECT, "t1")]


async def test_a_project_with_nothing_approved_costs_nothing(ws, store, monkeypatch):
    _patch_auth(monkeypatch, ws)
    _handoff(monkeypatch, ws, "the run's own work")
    _documents(monkeypatch, ws, "")
    routes = _record_route(monkeypatch, ws)
    calls = _record_run_agent(monkeypatch, ws)

    await _serve(ws, [_frame()])

    assert routes[0]["documents"] == ""
    assert calls[0][1]["context"] == "the run's own work"


# ── the failure posture ─────────────────────────────────────────────────────


async def test_a_failed_documents_read_refuses_the_turn(ws, store, monkeypatch):
    """The posture `handoff_context` and `attachment_context` already take here.

    Routing anyway would have the Orchestrator tell the user the project holds no
    documents about a BRD they can see on the Requirements page, and they would read
    that answer as informed. A visible refusal is recoverable; that is not.
    """
    from agents_orchestrator.orchestrator2 import project_documents as pd

    async def _unavailable(project_id, tenant_id):
        raise pd.ProjectDocumentsUnavailableError("database is on fire")

    _patch_auth(monkeypatch, ws)
    _handoff(monkeypatch, ws, "")
    routes = _record_route(monkeypatch, ws)
    calls = _record_run_agent(monkeypatch, ws)
    monkeypatch.setattr(ws, "approved_documents_context", _unavailable)

    socket = await _serve(ws, [_frame()])

    assert not routes, "the router must not answer a turn whose record could not be read"
    assert not calls, "no agent may run on it either"
    types = [e["type"] for e in socket.sent]
    assert "error" in types, "the refusal must be visible to the user"
    assert types[-1] == "stream_end", "a failed turn still has to hand the composer back"
