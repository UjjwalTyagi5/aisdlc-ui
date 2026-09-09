"""Trace attribution — the tags and ids the Traces page filters on must always be written.

WHY A TEST LIKE THIS EXISTS. `langfuse_langchain_extras` takes nine optional keyword
arguments, so a call site that forgets one is not an error — it is a default. Fifteen
hand-written call sites drifted exactly that way: only five passed `user_id`, three
passed `workspace_id`, and two passed no `tenant_id` at all. Nothing failed. The only
symptom was a Traces page that could not answer "show me this person's runs" for
two-thirds of agent traffic, and could not group by business unit for almost any of it.

So the guarantee is structural, not per-call-site: agent routes go through
`agent_trace`, which resolves identity centrally, and the first test below is what
keeps it that way when someone adds the sixteenth route.
"""
from __future__ import annotations

import pathlib
import re

import pytest

from shared.observability import agent_trace

AGENTS_DIR = pathlib.Path(__file__).resolve().parents[2] / "agents_orchestrator"


def test_no_agent_route_calls_the_low_level_builder_directly():
    """Every agent route must go through agent_trace, not langfuse_langchain_extras.

    This is the drift guard. Calling the builder directly is what allowed a route to
    ship with no user or no tenant, and no test noticed because the arguments are
    optional by design (they have to be — the builder is also used where there is no
    request to resolve identity from).
    """
    offenders = [
        str(p.relative_to(AGENTS_DIR))
        for p in AGENTS_DIR.rglob("*.py")
        if "langfuse_langchain_extras(" in p.read_text(encoding="utf-8", errors="ignore")
    ]
    assert offenders == [], (
        "These agent modules call langfuse_langchain_extras directly and so can silently "
        "omit tenant/user/workspace attribution. Use agent_trace instead: " + ", ".join(offenders)
    )


def test_every_agent_route_passes_a_project_and_an_agent_type():
    """Both are required for the Traces page's project filter and per-agent metrics."""
    call = re.compile(r"agent_trace\((?P<args>.*?)\)\s*$", re.S | re.M)
    checked = 0
    for path in AGENTS_DIR.rglob("*_api.py"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        for match in call.finditer(text):
            args = match.group("args")
            rel = path.relative_to(AGENTS_DIR)
            assert "agent_type=" in args, f"{rel}: agent_trace call without agent_type"
            assert "project_id=" in args, f"{rel}: agent_trace call without project_id"
            checked += 1
    # A regex that silently matches nothing would make this vacuously green.
    assert checked >= 10, f"expected the agent routes to be found, matched {checked}"


@pytest.mark.asyncio
async def test_agent_trace_resolves_identity_from_the_request(monkeypatch):
    """REST routes: tenant and user come from request.state, which the JWT middleware sets."""
    captured = {}

    def _capture(**kwargs):
        captured.update(kwargs)
        return [], {}

    monkeypatch.setattr("shared.observability.callbacks.langfuse_langchain_extras", _capture)

    class _State:
        user_id = "u-real"
        tenant_id = "t-real"

    class _Request:
        state = _State()

    await agent_trace(request=_Request(), session_id="s1", agent_type="design")

    assert captured["user_id"] == "u-real"
    assert captured["tenant_id"] == "t-real"


@pytest.mark.asyncio
async def test_agent_trace_resolves_identity_from_ws_ticket_claims(monkeypatch):
    """WebSocket routes have no Request — identity comes from the redeemed ticket."""
    captured = {}

    def _capture(**kwargs):
        captured.update(kwargs)
        return [], {}

    monkeypatch.setattr("shared.observability.callbacks.langfuse_langchain_extras", _capture)

    await agent_trace(
        claims={"user_id": "u-ws", "tenant_id": "t-ws"},
        session_id="s2",
        agent_type="testing",
    )

    assert captured["user_id"] == "u-ws"
    assert captured["tenant_id"] == "t-ws"


@pytest.mark.asyncio
async def test_run_id_reaches_trace_metadata(monkeypatch):
    """traces.py maps metadata["run_id"] -> TraceListItem.runId; nothing used to write it.

    Without this the read side looked for a key no writer produced, so every row's
    runId came back null and a trace could not be tied to the run that produced it.
    """
    monkeypatch.setattr(
        "shared.observability.callbacks.get_langfuse_client", lambda: object()
    )
    from shared.observability.callbacks import langfuse_langchain_extras

    _cbs, meta = langfuse_langchain_extras(
        session_id="s3", run_id="run-42", tenant_id="t1", agent_type="design"
    )
    assert meta.get("run_id") == "run-42"


@pytest.mark.asyncio
async def test_scope_tags_carry_the_full_hierarchy(monkeypatch):
    """tenant / workspace / project — the three tags the read side filters on.

    workspace is the business unit; the Traces view's unit scoping has nothing to
    filter on without it, which is why agent_trace resolves it rather than trusting
    each call site to remember.
    """
    monkeypatch.setattr(
        "shared.observability.callbacks.get_langfuse_client", lambda: object()
    )
    from shared.observability.callbacks import langfuse_langchain_extras

    _cbs, meta = langfuse_langchain_extras(
        session_id="s4", tenant_id="t1", project_id="p1", workspace_id="w1",
        user_id="u1", agent_type="requirements",
    )
    assert set(meta["langfuse_tags"]) == {"tenant:t1", "workspace:w1", "project:p1"}
    assert meta["langfuse_user_id"] == "u1"
