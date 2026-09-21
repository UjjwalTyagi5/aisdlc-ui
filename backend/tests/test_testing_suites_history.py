"""The Testing page's history: the page opens empty, and what came before is listed here.

LIVE: the page poured the last unit run into step 2 on arrival — 15 passed, from a run whose
test cases had since been deleted — while step 1 said nothing had been generated.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents_orchestrator.testing_agent.suites import jobs  # noqa: E402
from agents_orchestrator.testing_agent.suites.history import build_history  # noqa: E402
from shared.routers import testing_suites as api  # noqa: E402

PROJECT = "11111111-1111-1111-1111-111111111111"


@pytest.fixture(autouse=True)
def isolated_jobs(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "JOBS_DIR", tmp_path)
    jobs._jobs.clear()
    yield
    jobs._jobs.clear()


def _gen(id_, at, docs, status="succeeded", branch="feature/x"):
    return jobs.Job(id=id_, kind="generate", project_id=PROJECT, tenant_id="t1", user_id="u1", user_name="Sarthak",
                    status=status, created_at=at, finished_at=at,
                    params={"target": {"ado_project": "Q", "repo": "Q", "branch": branch}, "kinds": ["unit", "api"]},
                    result={"documents": [{"kind": k, "name": f"{k}.xlsx", "artifact_id": d} for k, d in docs]})


def _run(id_, at, doc, kind="run_unit", status="succeeded"):
    return jobs.Job(id=id_, kind=kind, project_id=PROJECT, tenant_id="t1", user_id="u2", user_name="Marcus",
                    status=status, created_at=at, finished_at=at if status == "succeeded" else None,
                    params={"document_id": doc, "suite_kind": kind.removeprefix("run_")},
                    result={"verdict": "Passed", "rows": []})


def test_runs_sit_under_the_generation_that_filed_their_test_cases():
    g1 = _gen("g1", "2026-09-17T10:00:00+00:00", [("unit", "d-u1"), ("api", "d-a1")])
    g2 = _gen("g2", "2026-09-17T11:00:00+00:00", [("unit", "d-u2")])
    runs = [_run("r1", "2026-09-17T10:05:00+00:00", "d-u1"),
            _run("r2", "2026-09-17T10:06:00+00:00", "d-a1", kind="run_api"),
            _run("r3", "2026-09-17T11:05:00+00:00", "d-u2")]
    entries = build_history([g1, g2, *runs])
    assert [e["id"] for e in entries] == ["g2", "g1"]
    assert [r["id"] for r in entries[1]["runs"]] == ["r2", "r1"]  # newest first
    assert [r["id"] for r in entries[0]["runs"]] == ["r3"]
    assert entries[0]["generation"]["user_name"] == "Sarthak" and "tenant_id" not in entries[0]["generation"]


def test_an_old_generation_run_again_today_moves_to_the_top():
    g1 = _gen("g1", "2026-09-16T10:00:00+00:00", [("unit", "d-u1")])
    g2 = _gen("g2", "2026-09-17T09:00:00+00:00", [("unit", "d-u2")])
    entries = build_history([g1, g2, _run("r1", "2026-09-17T12:00:00+00:00", "d-u1")])
    assert [e["id"] for e in entries] == ["g1", "g2"]
    assert entries[0]["updated_at"] == "2026-09-17T12:00:00+00:00"


def test_a_run_of_a_workbook_no_generation_filed_is_an_entry_of_its_own():
    g1 = _gen("g1", "2026-09-17T10:00:00+00:00", [("unit", "d-u1")])
    entries = build_history([g1, _run("r9", "2026-09-17T12:00:00+00:00", "d-uploaded", kind="run_functional")])
    assert entries[0]["id"] == "r9" and entries[0]["generation"] is None
    assert [r["id"] for r in entries[0]["runs"]] == ["r9"] and entries[1]["runs"] == []


def test_work_in_progress_marks_its_entry_active():
    g1 = _gen("g1", "2026-09-17T10:00:00+00:00", [("unit", "d-u1")])
    entries = build_history([g1, _run("r1", "2026-09-17T10:05:00+00:00", "d-u1", status="running")])
    assert entries[0]["active"] is True
    assert build_history([_gen("g2", "2026-09-17T10:00:00+00:00", [], status="failed")])[0]["active"] is False


def test_a_job_saved_before_names_were_recorded_still_loads(tmp_path):
    folder = tmp_path / PROJECT
    folder.mkdir()
    old = {"id": "g0", "kind": "generate", "project_id": PROJECT, "tenant_id": "t1", "user_id": "u1",
           "status": "succeeded", "created_at": "2026-09-17T09:00:00+00:00", "started_at": None,
           "finished_at": "2026-09-17T09:01:00+00:00", "progress": [], "result": {"documents": []},
           "error": "", "params": {}}
    (folder / "g0.json").write_text(json.dumps(old), encoding="utf-8")
    [job] = jobs.all_jobs(PROJECT)
    assert job.id == "g0" and job.user_name == ""


async def test_the_history_routes_page_and_open_one_entry():
    for g in (_gen("g1", "2026-09-17T10:00:00+00:00", [("unit", "d-u1")]),
              _gen("g2", "2026-09-17T11:00:00+00:00", [("unit", "d-u2")]),
              _run("r1", "2026-09-17T11:30:00+00:00", "d-u2")):
        jobs._save(g)
    page = await api.list_history(PROJECT, limit=1, offset=0)
    assert page["total"] == 2 and [e["id"] for e in page["entries"]] == ["g2"]
    assert [e["id"] for e in (await api.list_history(PROJECT, limit=1, offset=1))["entries"]] == ["g1"]
    opened = await api.get_history_entry(PROJECT, "g2")
    assert [r["id"] for r in opened["runs"]] == ["r1"]
    with pytest.raises(HTTPException) as err:
        await api.get_history_entry(PROJECT, "nope")
    assert err.value.status_code == 404


def test_history_is_not_read_as_a_document_id():
    """`/suites/{document_id}` would swallow `/suites/history` if it were declared first."""
    paths = [r.path for r in api.testing_suites_router.routes]
    assert paths.index("/{project_id}/suites/history") < paths.index("/{project_id}/suites/{document_id}")
    assert paths.index("/{project_id}/suites/history/{entry_id}") < paths.index("/{project_id}/suites/{document_id}")


async def test_whoever_starts_a_generation_is_recorded_by_name():
    async def done(job, **_kw):
        return {"documents": []}

    request = SimpleNamespace(state=SimpleNamespace(tenant_id="t1", user_id="u1"))
    body = api.GenerateRequest(target={"ado_project": "Q", "repo": "Q", "branch": "feature/x"})
    with patch.object(api, "_may_use_testing", AsyncMock(return_value=("t1", "u1", "Sarthak Kumar"))), \
         patch("agents_orchestrator.testing_agent.suites.workflows.generate_workflow", done):
        started = await api.generate(PROJECT, body, request)
    assert started["user_name"] == "Sarthak Kumar"
    assert json.loads((jobs.JOBS_DIR / PROJECT / f"{started['id']}.json").read_text())["user_name"] == "Sarthak Kumar"
