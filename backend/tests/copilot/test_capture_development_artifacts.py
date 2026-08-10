import pytest

from agents_orchestrator.orchestrator import copilot_api


@pytest.mark.asyncio
async def test_capture_development_artifacts_persists_and_returns_dict(monkeypatch):
    dev_artifacts = {"repo_url": "u", "branch_name": "b"}
    persisted = []

    async def _fake_fetch_session_artifacts(session_id):
        assert session_id == "r"
        return {"development_artifacts": dev_artifacts}

    async def _fake_persist_run_artifact(run_id, tenant_id, column, value, **kwargs):
        persisted.append((run_id, tenant_id, column, value))

    monkeypatch.setattr(copilot_api, "fetch_session_artifacts", _fake_fetch_session_artifacts)
    monkeypatch.setattr(copilot_api, "_persist_run_artifact", _fake_persist_run_artifact)

    result = await copilot_api._capture_development_artifacts("r", "t")

    assert result == dev_artifacts
    assert len(persisted) == 1
    run_id, tenant_id, column, value = persisted[0]
    assert run_id == "r"
    assert tenant_id == "t"
    assert column == "development_artifacts"
    assert value == dev_artifacts


@pytest.mark.asyncio
async def test_capture_development_artifacts_none_when_missing(monkeypatch):
    persisted = []

    async def _fake_fetch_session_artifacts(session_id):
        return {}

    async def _fake_persist_run_artifact(run_id, tenant_id, column, value, **kwargs):
        persisted.append((run_id, tenant_id, column, value))

    monkeypatch.setattr(copilot_api, "fetch_session_artifacts", _fake_fetch_session_artifacts)
    monkeypatch.setattr(copilot_api, "_persist_run_artifact", _fake_persist_run_artifact)

    result = await copilot_api._capture_development_artifacts("r", "t")

    assert result is None
    assert persisted == []
