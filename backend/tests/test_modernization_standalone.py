"""Track 3's standalone surfaces: the two agents' sockets and the read endpoints.

Real test database for everything that decides access (projects, tracks, bindings);
the socket is driven through a scripted fake the way tests/orchestrator2/
test_ws_access.py drives the Orchestrator's, so no network or model is involved.
"""
from __future__ import annotations

import json
import uuid as _uuid

import pytest
from fastapi import WebSocketDisconnect
from fastapi.testclient import TestClient
from sqlalchemy import text

import process_api
from config.auth.jwt import create_access_token
from shared.authz.grant import grant_role
from shared.db import get_db_session_for_tenant, get_db_session_superuser

pytestmark = pytest.mark.usefixtures("purge_created_orgs")


@pytest.fixture(autouse=True)
async def _dispose_shared_engine():
    yield
    from shared.db import engine
    await engine.dispose()


class _FakeWebSocket:
    def __init__(self, *, params=None, inbound=None):
        self.query_params = dict(params or {})
        self._inbound = [json.dumps(m) for m in (inbound or [])]
        self.accepted = False
        self.closed = None
        self.sent: list[dict] = []

    async def accept(self):
        self.accepted = True

    async def close(self, code=1000, reason=""):
        self.closed = (code, reason)

    async def receive_text(self):
        if not self._inbound:
            raise WebSocketDisconnect()
        return self._inbound.pop(0)

    async def send_text(self, raw):
        self.sent.append(json.loads(raw))


@pytest.fixture
async def org_with_tracks():
    org, unit = str(_uuid.uuid4()), str(_uuid.uuid4())
    modern, green = str(_uuid.uuid4()), str(_uuid.uuid4())
    async with get_db_session_superuser() as s:
        await s.execute(text(
            "INSERT INTO organizations (id, slug, display_name) VALUES (:i, :s, 'Mod Test')"
        ), {"i": org, "s": f"mod-{org[:8]}"})
        await s.execute(text(
            "INSERT INTO workspaces (id, organization_id, slug, display_name) "
            "VALUES (:i, :o, 'unit', 'Unit')"
        ), {"i": unit, "o": org})
    async with get_db_session_for_tenant(org) as s:
        for pid, name, track in ((modern, "Legacy Billing", "modernization"),
                                 (green, "Shiny New App", "greenfield")):
            await s.execute(text(
                "INSERT INTO projects (id, workspace_id, tenant_id, display_name, track) "
                "VALUES (:i, :w, :t, :n, :tr)"
            ), {"i": pid, "w": unit, "t": org, "n": name, "tr": track})
    yield {"org": org, "modern": modern, "green": green}


# ── the sockets ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("api_module,handler", [
    ("agents_orchestrator.discovery_agent.discovery_agent_api", "discovery_ws"),
    ("agents_orchestrator.requirements_modernization_agent.requirements_modernization_agent_api",
     "requirements_modernization_ws"),
])
async def test_a_socket_without_a_valid_ticket_is_closed_4401(monkeypatch, api_module, handler):
    import importlib

    async def reject(ticket):
        return None

    module = importlib.import_module(api_module)
    monkeypatch.setattr(module, "_redeem_ws_ticket", reject)
    socket = _FakeWebSocket(params={"ticket": "expired"})
    await getattr(module, handler)(socket)
    assert socket.accepted is False
    assert socket.closed[0] == 4401


async def test_a_greenfield_projects_turn_is_refused_and_the_turn_still_ends(monkeypatch, org_with_tracks):
    from agents_orchestrator.discovery_agent import discovery_agent_api

    t = org_with_tracks
    user = f"ba-{_uuid.uuid4()}"
    await grant_role(user, t["green"], "ba", tenant_id=t["org"], scope_kind="project")

    async def redeem(ticket):
        return {"user_id": user, "tenant_id": t["org"]}

    monkeypatch.setattr(discovery_agent_api, "_redeem_ws_ticket", redeem)
    socket = _FakeWebSocket(params={"ticket": "ok"}, inbound=[{
        "type": "user_message_with_files", "session_id": f"s-{_uuid.uuid4()}",
        "task_intent": "assess the repo", "pipeline_context": {"project_id": t["green"]},
    }])
    await discovery_agent_api.discovery_ws(socket)

    types = [e.get("type") for e in socket.sent]
    refusal = next(e for e in socket.sent if e.get("type") == "agent_response")
    assert "not part of this project's delivery track" in refusal["message"]
    # The end of the turn arrives on the refusal path too, or the composer hangs.
    assert types[-2:] == ["stream_end", "activity_update"]
    assert socket.sent[-1]["activity"]["type"] == "complete"


async def test_a_turn_with_no_project_is_refused_and_ends(monkeypatch):
    from agents_orchestrator.requirements_modernization_agent import (
        requirements_modernization_agent_api as api,
    )

    async def redeem(ticket):
        return {"user_id": "u1", "tenant_id": str(_uuid.uuid4())}

    monkeypatch.setattr(api, "_redeem_ws_ticket", redeem)
    socket = _FakeWebSocket(params={"ticket": "ok"}, inbound=[{
        "type": "user_message_with_files", "session_id": "s1", "task_intent": "hello",
    }])
    await api.requirements_modernization_ws(socket)
    assert any("named no project" in (e.get("message") or "") for e in socket.sent)
    assert socket.sent[-1]["type"] == "activity_update"


# ── the read endpoints ───────────────────────────────────────────────────────


def _hdr(user_id: str, org: str) -> dict:
    return {"Authorization": "Bearer " + create_access_token(
        user_id=user_id, tenant_id=org, permissions=["artifact:view"])}


async def _run_with(t: dict, column: str, payload: dict, updated: str) -> str:
    from datetime import datetime

    run_id = str(_uuid.uuid4())
    async with get_db_session_for_tenant(t["org"]) as s:
        await s.execute(text(
            f"INSERT INTO runs (id, project_id, tenant_id, stage, status, trigger, {column}, updated_at) "
            f"VALUES (:i, :p, :t, 'discovery', 'completed', 'chat', CAST(:j AS jsonb), :u)"
        ), {"i": run_id, "p": t["modern"], "t": t["org"], "j": json.dumps(payload),
            "u": datetime.fromisoformat(updated)})
    return run_id


async def test_the_read_endpoint_returns_the_newest_assessment(org_with_tracks):
    t = org_with_tracks
    user = f"ba-{_uuid.uuid4()}"
    await grant_role(user, t["modern"], "ba", tenant_id=t["org"], scope_kind="project")
    await _run_with(t, "discovery_artifacts", {"schema_version": 1, "tag": "old"}, "2026-09-01T00:00:00Z")
    newest = await _run_with(t, "discovery_artifacts", {"schema_version": 1, "tag": "new"}, "2026-09-09T00:00:00Z")

    r = TestClient(process_api.app).get(
        f"/projects/{t['modern']}/modernization/discovery", headers=_hdr(user, t["org"]))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["runId"] == newest
    assert body["payload"]["tag"] == "new"


async def test_the_read_endpoint_says_null_when_nothing_is_recorded(org_with_tracks):
    t = org_with_tracks
    user = f"ba-{_uuid.uuid4()}"
    await grant_role(user, t["modern"], "ba", tenant_id=t["org"], scope_kind="project")
    r = TestClient(process_api.app).get(
        f"/projects/{t['modern']}/modernization/migration-intent", headers=_hdr(user, t["org"]))
    assert r.status_code == 200, r.text
    assert r.json()["payload"] is None


async def test_the_read_endpoint_refuses_a_role_that_does_not_reach_the_agent(org_with_tracks):
    t = org_with_tracks
    user = f"dev-{_uuid.uuid4()}"
    await grant_role(user, t["modern"], "developer", tenant_id=t["org"], scope_kind="project")
    r = TestClient(process_api.app).get(
        f"/projects/{t['modern']}/modernization/discovery", headers=_hdr(user, t["org"]))
    assert r.status_code == 403


async def test_the_read_endpoint_refuses_a_greenfield_project(org_with_tracks):
    t = org_with_tracks
    user = f"ba-{_uuid.uuid4()}"
    await grant_role(user, t["green"], "ba", tenant_id=t["org"], scope_kind="project")
    r = TestClient(process_api.app).get(
        f"/projects/{t['green']}/modernization/migration-intent", headers=_hdr(user, t["org"]))
    assert r.status_code == 403
