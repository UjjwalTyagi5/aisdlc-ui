"""Restart-survival re-prepare fallback for `_run_dev_work_dir` (Task 7).

After a backend restart the in-memory dev session (session_state.get_session) is
gone, and if the legacy on-disk `files/<user>/orchestrator/<run_id>/project` clone
never existed for this run (or was also wiped), the resolver used to return None —
the Copilot workspace tree/file endpoints would show "nothing cloned" even though
the run has a perfectly good `runs.development_artifacts.repo_url` to re-clone from.

This asserts the two new fallback tiers:
  1. Reuse the Bridge's run-keyed clone (`run_workspace._run_dir`) if it's already
     there (covered indirectly — `_is_existing_clone` returns False here since the
     dir under a throwaway run_id never exists, so we fall through to tier 2).
  2. Re-prepare it via `prepare_run_workspace` from the persisted development_artifacts
     when nothing is on disk at all.
"""
import glob as glob_module

import pytest

from shared.routers import runs as runs_module


@pytest.mark.asyncio
async def test_reprepares_workspace_from_persisted_dev_artifacts(monkeypatch, tmp_path):
    run_id = "11111111-1111-1111-1111-111111111111"
    dev_artifacts = {"repo_url": "https://example/repo", "branch_name": "feature/x", "base_sha": "abc123"}

    # In-memory session miss.
    monkeypatch.setattr(
        "agents_orchestrator.development_agent.config.session_state.get_session",
        lambda rid: None,
    )
    # On-disk legacy glob miss.
    monkeypatch.setattr(glob_module, "glob", lambda *a, **k: [])
    # No Bridge run-keyed clone on disk either.
    monkeypatch.setattr(
        "shared.services.run_workspace._is_existing_clone", lambda p: False
    )

    calls = []

    async def _fake_resolve_auth(tenant_id=""):
        return ("org", "pat-123")

    async def _fake_prepare_run_workspace(rid, repo_url, ref=None, base=None, *, pat=None):
        calls.append({"run_id": rid, "repo_url": repo_url, "ref": ref, "base": base, "pat": pat})

        class _WS:
            work_dir = str(tmp_path / "reprepared")

        return _WS()

    monkeypatch.setattr("shared.services.ado_repos.resolve_auth", _fake_resolve_auth)
    monkeypatch.setattr(
        "shared.services.run_workspace.prepare_run_workspace", _fake_prepare_run_workspace
    )

    result = await runs_module._run_dev_work_dir(run_id, dev_artifacts, "tenant-1")

    assert result == str(tmp_path / "reprepared")
    assert len(calls) == 1
    assert calls[0]["run_id"] == run_id
    assert calls[0]["repo_url"] == "https://example/repo"
    assert calls[0]["ref"] == "feature/x"
    assert calls[0]["base"] == "abc123"
    assert calls[0]["pat"] == "pat-123"


@pytest.mark.asyncio
async def test_returns_none_when_no_artifacts_and_nothing_on_disk(monkeypatch):
    run_id = "22222222-2222-2222-2222-222222222222"

    monkeypatch.setattr(
        "agents_orchestrator.development_agent.config.session_state.get_session",
        lambda rid: None,
    )
    monkeypatch.setattr(glob_module, "glob", lambda *a, **k: [])
    monkeypatch.setattr(
        "shared.services.run_workspace._is_existing_clone", lambda p: False
    )

    result = await runs_module._run_dev_work_dir(run_id, None, "tenant-1")

    assert result is None


@pytest.mark.asyncio
async def test_reuses_existing_run_keyed_clone_without_reprepare(monkeypatch, tmp_path):
    """Tier 1 fallback: the Bridge already cloned this run — reuse it, never call
    prepare_run_workspace (no network / re-clone needed)."""
    run_id = "33333333-3333-3333-3333-333333333333"

    monkeypatch.setattr(
        "agents_orchestrator.development_agent.config.session_state.get_session",
        lambda rid: None,
    )
    monkeypatch.setattr(glob_module, "glob", lambda *a, **k: [])
    monkeypatch.setattr(
        "shared.services.run_workspace._run_dir", lambda rid: tmp_path
    )
    monkeypatch.setattr(
        "shared.services.run_workspace._is_existing_clone", lambda p: True
    )

    async def _fail_prepare(*a, **k):
        raise AssertionError("prepare_run_workspace must not be called when clone already exists")

    monkeypatch.setattr("shared.services.run_workspace.prepare_run_workspace", _fail_prepare)

    result = await runs_module._run_dev_work_dir(
        run_id, {"repo_url": "https://example/repo"}, "tenant-1"
    )

    assert result == str(tmp_path)
