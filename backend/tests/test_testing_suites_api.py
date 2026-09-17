"""The suites API: who may start work, one generation at a time, and jobs that say what happened."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents_orchestrator.testing_agent.suites import jobs  # noqa: E402
from shared.routers import testing_suites as api  # noqa: E402

PROJECT = "11111111-1111-1111-1111-111111111111"


def _request():
    return SimpleNamespace(state=SimpleNamespace(tenant_id="t1", user_id="u1"))


@pytest.fixture(autouse=True)
def isolated_jobs(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "JOBS_DIR", tmp_path)
    jobs._jobs.clear()
    yield
    jobs._jobs.clear()


def _body():
    return api.GenerateRequest(target={"ado_project": "QUICKLINK(Url shortner)", "repo": "QUICKLINK(Url shortner)",
                                       "branch": "feature/x"}, offering_id="off-grok")


async def test_generation_needs_the_testing_agent_on_the_project():
    with patch.object(api, "_may_use_testing", AsyncMock(side_effect=HTTPException(403, "no access"))):
        with pytest.raises(HTTPException) as err:
            await api.generate(PROJECT, _body(), _request())
    assert err.value.status_code == 403 and not jobs._jobs


async def test_a_second_generation_while_one_runs_is_refused():
    gate = asyncio.Event()

    async def slow(job, **_kw):
        await gate.wait()
        return {"documents": []}

    with patch.object(api, "_may_use_testing", AsyncMock(return_value=("t1", "u1"))), \
         patch("agents_orchestrator.testing_agent.suites.workflows.generate_workflow", slow):
        first = await api.generate(PROJECT, _body(), _request())
        await asyncio.sleep(0)
        with pytest.raises(HTTPException) as err:
            await api.generate(PROJECT, _body(), _request())
        assert err.value.status_code == 409
        assert first["kinds"] if False else first["params"]["kinds"] == ["unit", "functional", "api"]
        gate.set()
        for _ in range(50):
            if jobs.get_job(PROJECT, first["id"]).status == "succeeded":
                break
            await asyncio.sleep(0.01)
    assert (await api.get_suite_job(PROJECT, first["id"]))["status"] == "succeeded"


async def test_a_failed_job_keeps_its_reason_and_survives_a_restart():
    async def boom(job, **_kw):
        await jobs.log(job, "Checking out QUICKLINK @ feature/x")
        raise RuntimeError("git clone failed: repository not found")

    with patch.object(api, "_may_use_testing", AsyncMock(return_value=("t1", "u1"))), \
         patch("agents_orchestrator.testing_agent.suites.workflows.generate_workflow", boom):
        started = await api.generate(PROJECT, _body(), _request())
        for _ in range(50):
            if jobs.get_job(PROJECT, started["id"]).status == "failed":
                break
            await asyncio.sleep(0.01)
    jobs._jobs.clear()  # a restart: only the disk copy remains
    listed = (await api.list_suite_jobs(PROJECT))["jobs"]
    assert listed[0]["status"] == "failed" and "repository not found" in listed[0]["error"]
    assert [p["message"] for p in listed[0]["progress"]][0] == "Checking out QUICKLINK @ feature/x"
    assert "tenant_id" not in listed[0]


def test_a_job_running_when_the_backend_stopped_is_reported_interrupted(tmp_path):
    job = jobs.Job(id="j1", kind="generate", project_id=PROJECT, tenant_id="t1", user_id="u1", status="running")
    jobs._save(job)
    [loaded] = jobs.list_jobs(PROJECT)
    assert loaded.status == "interrupted" and "restarted" in loaded.error


async def test_reading_a_document_that_is_not_a_suite_says_so():
    from agents_orchestrator.testing_agent.suites import store

    row = SimpleNamespace(id="d1", approval_status="draft")
    with patch.object(store, "document_bytes", AsyncMock(return_value=(row, b"not a workbook"))):
        with pytest.raises(HTTPException) as err:
            await api.read_suite_document(PROJECT, "d1", _request())
    assert err.value.status_code == 422 and "not a runnable test case suite" in err.value.detail
