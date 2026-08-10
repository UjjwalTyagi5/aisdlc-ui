"""`_maybe_switch_stage` — routes a turn to another agent when the user names it in
chat, RBAC-checked (mirrors the rail-click `set-stage` permission gate).

Monkeypatches `copilot_api.detect_switch` directly (Task 1's detector is unit-tested
in isolation in test_stage_switch.py) so these tests only exercise the wiring: RBAC
check, `_repoint_stage` invocation, `stage.changed` emission, and the fail-closed
permission-notice path. Mirrors the fake-websocket / fake-send-capture pattern used
in tests/copilot/test_requirements_card_gating.py.
"""
from __future__ import annotations

import pytest

from agents_orchestrator.orchestrator import copilot_api

RUN_ID = "run-switch-1"
TENANT_ID = "tenant-switch-1"


class _FakeWebSocket:
    async def send_text(self, text):
        pass


async def _detect_documentation(text, current_stage, *, llm_classify=None):
    return "documentation"


@pytest.mark.asyncio
async def test_admin_switch_repoints_stage_and_emits_stage_changed(monkeypatch):
    monkeypatch.setattr(copilot_api, "detect_switch", _detect_documentation)

    repoint_calls = []

    async def _fake_repoint(run_id, tenant_id, target, actor_id="system"):
        repoint_calls.append((run_id, tenant_id, target))

    monkeypatch.setattr(copilot_api, "_repoint_stage", _fake_repoint)

    sent = []

    async def _fake_send(websocket, payload):
        sent.append(payload)

    monkeypatch.setattr(copilot_api, "_send", _fake_send)

    persisted = []

    async def _fake_persist_turn(run_id, role, content, *, tenant_id=None, author_id=None):
        persisted.append((run_id, role, content, author_id))

    monkeypatch.setattr(copilot_api, "persist_turn", _fake_persist_turn)

    result = await copilot_api._maybe_switch_stage(
        "run documentation and create a summary", "development",
        RUN_ID, TENANT_ID, ["admin:*"], _FakeWebSocket())

    # A switch ACTIVATES the target agent but does NOT run its work: proceed=False
    # so the switch command isn't fed into the newly-activated agent (which would make
    # it auto-run its whole flow). The user's next message drives it.
    assert result == ("documentation", False)
    assert repoint_calls == [(RUN_ID, TENANT_ID, "documentation")]
    assert {"type": "stage.changed", "run_id": RUN_ID, "stage": "documentation"} in sent
    assert any(p[2].startswith("↳ Switched to the Documentation agent") for p in persisted)


@pytest.mark.asyncio
async def test_non_permitted_caller_gets_notice_and_stays_on_active(monkeypatch):
    monkeypatch.setattr(copilot_api, "detect_switch", _detect_documentation)

    repoint_calls = []

    async def _fake_repoint(run_id, tenant_id, target, actor_id="system"):
        repoint_calls.append((run_id, tenant_id, target))

    monkeypatch.setattr(copilot_api, "_repoint_stage", _fake_repoint)

    sent = []

    async def _fake_send(websocket, payload):
        sent.append(payload)

    monkeypatch.setattr(copilot_api, "_send", _fake_send)

    persisted = []

    async def _fake_persist_turn(run_id, role, content, *, tenant_id=None, author_id=None):
        persisted.append((run_id, role, content, author_id))

    monkeypatch.setattr(copilot_api, "persist_turn", _fake_persist_turn)

    # A developer role — no artifact:approve_documentation, no admin:* wildcard.
    result = await copilot_api._maybe_switch_stage(
        "run documentation and create a summary", "development",
        RUN_ID, TENANT_ID, ["run:create", "run:view"], _FakeWebSocket())

    assert result == ("development", False)
    assert repoint_calls == []
    assert not any(p.get("type") == "stage.changed" for p in sent)
    assert any("don't have permission to switch to Documentation" in p[2] for p in persisted)
