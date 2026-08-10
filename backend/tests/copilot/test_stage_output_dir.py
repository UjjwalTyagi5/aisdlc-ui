"""Unit tests for `_run_stage_output_dir` — the stage-aware output-dir resolver
that lets the Copilot artifacts panel browse ANY downstream agent's generated
files (not just Development's), reusing the existing generic FS layer.
"""
import os

import pytest

from shared.routers import runs as runs_module


@pytest.mark.asyncio
async def test_development_stage_delegates_to_dev_work_dir(monkeypatch):
    async def _fake_dev_work_dir(run_id, dev_artifacts, tenant_id):
        assert run_id == "r1"
        assert dev_artifacts == {"repo_url": "u"}
        assert tenant_id == "t1"
        return "/fake/dev/work_dir"

    monkeypatch.setattr(runs_module, "_run_dev_work_dir", _fake_dev_work_dir)

    result = await runs_module._run_stage_output_dir(
        "r1", "development", development_artifacts={"repo_url": "u"}, tenant_id="t1"
    )
    assert result == "/fake/dev/work_dir"


@pytest.mark.asyncio
async def test_testing_stage_returns_output_dir_when_present(tmp_path, monkeypatch):
    files_dir = tmp_path / "files"
    output_dir = files_dir / "alice" / "orchestrator" / "run-123" / "output"
    output_dir.mkdir(parents=True)

    monkeypatch.setattr(runs_module, "_stage_files_dir", lambda: str(files_dir))

    result = await runs_module._run_stage_output_dir("run-123", "testing")
    assert result == str(output_dir)


@pytest.mark.asyncio
async def test_testing_stage_returns_none_when_absent(tmp_path, monkeypatch):
    files_dir = tmp_path / "files"
    files_dir.mkdir()

    monkeypatch.setattr(runs_module, "_stage_files_dir", lambda: str(files_dir))

    result = await runs_module._run_stage_output_dir("run-404", "testing")
    assert result is None


@pytest.mark.asyncio
async def test_documentation_stage_returns_docs_root_dir(tmp_path, monkeypatch):
    docs_root = tmp_path / "generated-docs"
    doc_dir = docs_root / "proj-1" / "run-123"
    doc_dir.mkdir(parents=True)

    monkeypatch.setattr(runs_module, "_docs_output_root", lambda: str(docs_root))

    result = await runs_module._run_stage_output_dir(
        "run-123", "documentation", project_id="proj-1"
    )
    assert result == str(doc_dir)


@pytest.mark.asyncio
async def test_documentation_stage_returns_none_without_project_id(tmp_path, monkeypatch):
    docs_root = tmp_path / "generated-docs"
    monkeypatch.setattr(runs_module, "_docs_output_root", lambda: str(docs_root))

    result = await runs_module._run_stage_output_dir("run-123", "documentation", project_id=None)
    assert result is None


@pytest.mark.asyncio
async def test_documentation_stage_returns_none_when_dir_missing(tmp_path, monkeypatch):
    docs_root = tmp_path / "generated-docs"
    docs_root.mkdir()
    monkeypatch.setattr(runs_module, "_docs_output_root", lambda: str(docs_root))

    result = await runs_module._run_stage_output_dir(
        "run-123", "documentation", project_id="proj-1"
    )
    assert result is None


@pytest.mark.parametrize("stage", ["security", "code_review", "deployment"])
@pytest.mark.asyncio
async def test_generated_stages_return_generated_stage_dir(tmp_path, monkeypatch, stage):
    files_dir = tmp_path / "files"
    stage_dir = files_dir / "system" / "orchestrator" / "run-123" / "generated" / stage
    stage_dir.mkdir(parents=True)

    monkeypatch.setattr(runs_module, "_stage_files_dir", lambda: str(files_dir))

    result = await runs_module._run_stage_output_dir("run-123", stage)
    assert result == str(stage_dir)


@pytest.mark.parametrize("stage", ["security", "code_review", "deployment"])
@pytest.mark.asyncio
async def test_generated_stages_return_none_when_absent(tmp_path, monkeypatch, stage):
    files_dir = tmp_path / "files"
    files_dir.mkdir()
    monkeypatch.setattr(runs_module, "_stage_files_dir", lambda: str(files_dir))

    result = await runs_module._run_stage_output_dir("run-404", stage)
    assert result is None


@pytest.mark.asyncio
async def test_unknown_stage_returns_none():
    result = await runs_module._run_stage_output_dir("run-123", "not_a_real_stage")
    assert result is None
