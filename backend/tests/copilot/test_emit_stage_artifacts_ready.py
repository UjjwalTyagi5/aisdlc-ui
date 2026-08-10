"""Fix B — `_emit_stage_artifacts_ready` pushes a stage's persisted artifacts to the
LIVE panel (`artifact.ready`) right after they're written, so the panel doesn't go
blank after the stage advances (previously nothing pushed Development's code-tree/
summary/PR sections live — only a reload via `sections_from_run` showed them)."""
import json
from types import SimpleNamespace

import pytest

from agents_orchestrator.orchestrator import copilot_api


class _FakeWS:
    def __init__(self):
        self.sent = []

    async def send_text(self, text):
        self.sent.append(json.loads(text))


class _FakeAsyncCM:
    """Stand-in for `async with get_db_session_for_tenant(...) as s:` — mirrors the
    pattern already used in test_capture_stage_files.py."""
    def __init__(self, run):
        self._run = run

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def execute(self, *args, **kwargs):
        return SimpleNamespace(scalar_one_or_none=lambda: self._run)


@pytest.mark.asyncio
async def test_development_emits_code_tree_section(monkeypatch):
    run = SimpleNamespace(development_artifacts={
        "repo_url": "https://dev.azure.com/org/proj/_git/repo",
        "branch_name": "feature/x",
        "pr_url": "https://dev.azure.com/org/proj/_git/repo/pullrequest/1",
        "code_summary": "Implemented the widget.",
    })
    monkeypatch.setattr(copilot_api, "get_db_session_for_tenant",
                        lambda tenant_id: _FakeAsyncCM(run))
    ws = _FakeWS()

    await copilot_api._emit_stage_artifacts_ready("run-1", "tenant-1", "development", ws)

    assert len(ws.sent) == 1
    msg = ws.sent[0]
    assert msg["type"] == "artifact.ready"
    assert msg["stage"] == "development"
    ids = [a["id"] for a in msg["artifacts"]]
    kinds = [a["kind"] for a in msg["artifacts"]]
    assert "dev-code" in ids
    assert "code-tree" in kinds
    assert "dev-summary" in ids
    assert "dev-pr" in ids


@pytest.mark.asyncio
async def test_development_noop_when_no_artifacts_yet(monkeypatch):
    run = SimpleNamespace(development_artifacts=None)
    monkeypatch.setattr(copilot_api, "get_db_session_for_tenant",
                        lambda tenant_id: _FakeAsyncCM(run))
    ws = _FakeWS()

    await copilot_api._emit_stage_artifacts_ready("run-2", "tenant-1", "development", ws)

    assert ws.sent == []


@pytest.mark.asyncio
async def test_missing_tenant_id_is_noop():
    ws = _FakeWS()
    await copilot_api._emit_stage_artifacts_ready("run-3", "", "development", ws)
    assert ws.sent == []


@pytest.mark.asyncio
async def test_missing_run_row_is_noop(monkeypatch):
    monkeypatch.setattr(copilot_api, "get_db_session_for_tenant",
                        lambda tenant_id: _FakeAsyncCM(None))
    ws = _FakeWS()
    await copilot_api._emit_stage_artifacts_ready("run-4", "tenant-1", "development", ws)
    assert ws.sent == []


@pytest.mark.asyncio
async def test_db_error_is_fail_soft(monkeypatch):
    def _boom(tenant_id):
        raise RuntimeError("db down")
    monkeypatch.setattr(copilot_api, "get_db_session_for_tenant", _boom)
    ws = _FakeWS()
    # must not raise
    await copilot_api._emit_stage_artifacts_ready("run-5", "tenant-1", "development", ws)
    assert ws.sent == []
