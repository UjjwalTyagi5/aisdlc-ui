"""The Orchestrator's chat is persisted, so it can be reopened and continued.

Until now the rail was `zustand` + localStorage and said so on screen: "Sessions are
stored in this browser only." Clearing site data lost every chat; another device never
had them; none of it was auditable. Spec §5.3 called for server-backed sessions in
Phase 1, and Phases 1-4 never built them.

Almost none of this is new. `shared/services/conversation_service` is complete and
every standalone agent already uses it; `orchestrator2` only READ from it (the router
pulls history for routing) and never wrote. This module is the writer.
"""
import pytest

from agents_orchestrator.orchestrator2 import sessions

_RUN = "11111111-1111-1111-1111-111111111111"
_TENANT = "22222222-2222-2222-2222-222222222222"
_PROJECT = "33333333-3333-3333-3333-333333333333"
_USER = "44444444-4444-4444-4444-444444444444"


def test_the_agent_key_is_the_orchestrator_not_one_of_the_nine():
    """Sessions are listed per agent_id. Reusing one of the nine would mix Orchestrator
    chats into that standalone agent's own history rail."""
    from agents_orchestrator.orchestrator2.registry import AGENT_IDS

    assert sessions.ORCHESTRATOR_AGENT_KEY == "orchestrator"
    assert sessions.ORCHESTRATOR_AGENT_KEY not in AGENT_IDS


@pytest.mark.asyncio
async def test_the_session_id_is_the_run_id(monkeypatch):
    """One conversation is one run (spec §5.3). Keying the session on the run id is what
    makes reopening a chat also reopen its Deliverables, its LangGraph thread and its
    project scope — without a single line of new mapping."""
    captured = {}

    async def _ensure(session_id, tenant_id, **kwargs):
        captured["session_id"] = session_id
        captured["tenant_id"] = tenant_id
        captured.update(kwargs)

    monkeypatch.setattr(sessions.cs, "ensure_session_with_id", _ensure)
    await sessions.ensure_session(
        _RUN, tenant_id=_TENANT, project_id=_PROJECT, user_id=_USER,
        first_message="I need a PRD for billing",
    )
    assert captured["session_id"] == _RUN
    assert captured["run_id"] == _RUN
    assert captured["tenant_id"] == _TENANT
    assert str(captured["project_id"]) == _PROJECT
    assert captured["created_by"] == _USER
    assert captured["agent_id"] == "orchestrator"


@pytest.mark.asyncio
async def test_the_title_comes_from_the_first_message(monkeypatch):
    """A rail of chats all called "New chat" is a rail you cannot navigate."""
    captured = {}

    async def _ensure(session_id, tenant_id, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(sessions.cs, "ensure_session_with_id", _ensure)
    await sessions.ensure_session(
        _RUN, tenant_id=_TENANT, project_id=_PROJECT, user_id=_USER,
        first_message="I need a PRD for the billing rework",
    )
    assert "billing" in (captured.get("title") or "").lower()


def test_a_long_first_message_is_truncated_for_the_rail():
    title = sessions._title_from("x" * 500)
    assert len(title) <= 80, title
    assert title.endswith("…")


def test_an_empty_first_message_still_gets_a_label():
    assert sessions._title_from("   ") == "New chat"


@pytest.mark.asyncio
async def test_both_sides_of_a_turn_are_recorded(monkeypatch):
    turns = []

    async def _persist(session_id, role, content, *, tenant_id, author_id=None, **kw):
        turns.append((session_id, role, content, author_id))

    monkeypatch.setattr(sessions.cs, "persist_turn", _persist)
    await sessions.record_turn(_RUN, "user", "hello", tenant_id=_TENANT, user_id=_USER)
    await sessions.record_turn(_RUN, "agent", "hi back", tenant_id=_TENANT, user_id=_USER)
    assert [t[1] for t in turns] == ["user", "agent"]
    assert turns[0][0] == _RUN and turns[0][3] == _USER


@pytest.mark.asyncio
async def test_a_persistence_failure_never_raises(monkeypatch):
    """Losing a transcript row must never cost the user their turn — the same trade
    deliverable capture makes, and why `persist_turn` swallows internally."""
    async def _boom(*a, **k):
        raise RuntimeError("database gone")

    monkeypatch.setattr(sessions.cs, "ensure_session_with_id", _boom)
    monkeypatch.setattr(sessions.cs, "persist_turn", _boom)
    await sessions.ensure_session(_RUN, tenant_id=_TENANT, project_id=_PROJECT,
                                  user_id=_USER, first_message="x")
    await sessions.record_turn(_RUN, "user", "x", tenant_id=_TENANT, user_id=_USER)


@pytest.mark.asyncio
async def test_the_socket_records_the_user_turn_and_the_reply(monkeypatch):
    """Through ws.py, so the WIRING is covered and not only the helper.

    Reuses test_ws_routing.py's harness rather than new fakes, so a change to the
    socket's turn loop breaks both files together instead of leaving this one green
    against a shape that no longer exists.
    """
    from agents_orchestrator.orchestrator2 import router as rtr, ws
    from tests.orchestrator2.test_ws_routing import (
        _frame, _no_context, _patch_auth, _record_route, _record_run_agent, _serve,
    )

    recorded = []

    async def _record(run_id, role, content, *, tenant_id, user_id):
        recorded.append((run_id, role, content))

    ensured = []

    async def _ensure(run_id, *, tenant_id, project_id, user_id, first_message):
        ensured.append((run_id, first_message))

    monkeypatch.setattr(ws.sessions, "record_turn", _record)
    monkeypatch.setattr(ws.sessions, "ensure_session", _ensure)

    _patch_auth(monkeypatch, ws)
    _no_context(monkeypatch, ws)
    _record_route(monkeypatch, ws, rtr.RoutingDecision(
        agent_id="requirements", reason="r", direct_reply=None))
    _record_run_agent(monkeypatch, ws, [
        {"type": "stream_chunk", "content": "here is your PRD"},
        {"type": "stream_end"},
    ])

    await _serve(ws, [_frame(text="I need a PRD")])

    assert ensured and ensured[0][1] == "I need a PRD", (
        "the session is created from the first message, so the rail has a real title"
    )
    assert [(r[1], r[2]) for r in recorded] == [
        ("user", "I need a PRD"),
        ("agent", "here is your PRD"),
    ]
    assert {r[0] for r in recorded} == {_RUN}, "both sides file under the run id"
