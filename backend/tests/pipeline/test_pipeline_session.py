"""Pipeline Session Bridge tests (Task 4).

The session bind/clear assertions are the load-bearing contract and MUST pass with
NO database: the mirror (`upsert_agent_session`) is best-effort and the upstream read
degrades to {} on any DB error. We isolate from Postgres by monkeypatching
`_read_run_upstream` / `upsert_agent_session` (we do NOT fake the DB).

The live round-trip against a real `runs` row is marked integration + skip — there is
no `db`/`seed_run` fixture in this repo yet (deferred to the Task 16/17 live-verification),
matching the existing precedent in test_upstream_mirror.py.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

import workflows.activities.pipeline_session as ps_mod
from config.ws_helper import get_session_id
from workflows.activities.pipeline_session import PipelineSession, pipeline_session


@pytest.fixture
def no_db(monkeypatch):
    """Isolate the Bridge from Postgres: stub the upstream read and the mirror write."""
    captured = {}

    # `tenant_id` is now part of the signature and is load-bearing: `runs` is FORCE
    # RLS, so an unscoped read returns zero rows and the mirror silently carries
    # nothing across. Captured so a test can assert it was actually threaded.
    async def _fake_read(run_id, tenant_id=None):
        captured["read_tenant_id"] = tenant_id
        return {"requirements_payload": {"stories": []}}

    async def _fake_upsert(session_id, **kwargs):
        captured["session_id"] = session_id
        captured.update(kwargs)

    monkeypatch.setattr(ps_mod, "_read_run_upstream", _fake_read)
    monkeypatch.setattr(ps_mod, "upsert_agent_session", _fake_upsert)
    return captured


@pytest.mark.asyncio
async def test_binds_session_and_clears(no_db):
    inp = SimpleNamespace(run_id="abc-123", tenant_id=None, project_id="p1")
    assert get_session_id() is None
    async with pipeline_session(inp, "design") as ps:
        assert get_session_id() == "abc-123"
        assert ps._upstream["requirements_payload"] == {"stories": []}
        assert ps.work_dir is None  # needs_repo defaults to False
    assert get_session_id() is None  # cleared on exit


@pytest.mark.asyncio
async def test_clears_session_on_error(no_db):
    """The contextvar must be reset even when the body raises (try/finally)."""
    inp = SimpleNamespace(run_id="err-1", tenant_id=None)
    with pytest.raises(RuntimeError):
        async with pipeline_session(inp, "design"):
            assert get_session_id() == "err-1"
            raise RuntimeError("boom")
    assert get_session_id() is None


@pytest.mark.asyncio
async def test_mirror_passes_only_present_upstream(no_db, monkeypatch):
    """Only non-None upstream fields are mirrored; agent_type/current_stage carry agent_id."""
    async def _fake_read(run_id, tenant_id=None):
        return {"requirements_payload": {"stories": [1]}, "design_artifacts": None}

    monkeypatch.setattr(ps_mod, "_read_run_upstream", _fake_read)
    inp = SimpleNamespace(run_id="r-2", tenant_id="t-9")
    async with pipeline_session(inp, "development"):
        pass
    assert no_db["session_id"] == "r-2"
    assert no_db["agent_type"] == "development"
    assert no_db["current_stage"] == "development"
    assert no_db["tenant_id"] == "t-9"
    assert no_db["requirements_payload"] == {"stories": [1]}
    assert "design_artifacts" not in no_db  # None upstream is dropped


@pytest.mark.asyncio
async def test_degrades_when_upstream_read_fails(monkeypatch):
    """needs_repo=False must work with zero usable DB: read returns {}, no crash."""
    async def _empty_read(run_id, tenant_id=None):
        return {}

    async def _noop_upsert(session_id, **kwargs):
        return None

    monkeypatch.setattr(ps_mod, "_read_run_upstream", _empty_read)
    monkeypatch.setattr(ps_mod, "upsert_agent_session", _noop_upsert)
    inp = SimpleNamespace(run_id="r-3")
    async with pipeline_session(inp, "testing") as ps:
        assert get_session_id() == "r-3"
        assert ps._upstream == {}
        assert ps.work_dir is None
    assert get_session_id() is None


@pytest.mark.asyncio
async def test_clones_when_needs_repo(no_db, monkeypatch):
    """needs_repo + repo_ref clones via prepare_run_workspace and exposes work_dir."""
    from shared.services.run_workspace import RunWorkspace

    calls = {}

    async def _fake_prepare(run_id, repo_url, ref, base=None, *, pat=None):
        calls.update(run_id=run_id, repo_url=repo_url, ref=ref, base=base, pat=pat)
        return RunWorkspace(work_dir="/ws/run/r-4/repo", diff_text="d", changed_files=["a.py"])

    monkeypatch.setattr(ps_mod, "prepare_run_workspace", _fake_prepare)
    inp = SimpleNamespace(run_id="r-4", tenant_id=None)
    repo_ref = {"repo_url": "https://dev.azure.com/o/p/_git/r", "ref": "feature", "base": "main"}
    async with pipeline_session(inp, "code_review", needs_repo=True, repo_ref=repo_ref) as ps:
        assert ps.work_dir == "/ws/run/r-4/repo"
        assert ps._workspace.changed_files == ["a.py"]
    assert calls["repo_url"].endswith("/_git/r")
    assert calls["ref"] == "feature"
    assert calls["base"] == "main"


@pytest.mark.asyncio
async def test_no_clone_when_needs_repo_but_no_repo_ref(no_db, monkeypatch):
    """needs_repo=True but no repo_url → no clone attempted, work_dir stays None."""
    def _boom(*a, **k):
        raise AssertionError("prepare_run_workspace must not be called")

    monkeypatch.setattr(ps_mod, "prepare_run_workspace", _boom)
    inp = SimpleNamespace(run_id="r-5", tenant_id=None)
    async with pipeline_session(inp, "security", needs_repo=True, repo_ref=None) as ps:
        assert ps.work_dir is None


def test_captured_artifact_reads_session_state():
    ps = PipelineSession(run_id="r-6")
    state = {"r-6": {"findings": [1, 2]}}
    assert ps.captured_artifact(lambda sid: state.get(sid)) == {"findings": [1, 2]}


def test_captured_artifact_swallows_getter_error():
    ps = PipelineSession(run_id="r-7")

    def _raise(_sid):
        raise KeyError("nope")

    assert ps.captured_artifact(_raise) is None


@pytest.mark.integration
@pytest.mark.skip(reason="Requires 'db'/'seed_run' fixture with live Postgres — run in Task 16/17 live-verification")
@pytest.mark.asyncio
async def test_binds_session_and_clears_live(db, seed_run):  # pragma: no cover
    run_id = seed_run(requirements_payload={"stories": []})
    inp = SimpleNamespace(run_id=run_id, tenant_id=None, project_id="p1")
    async with pipeline_session(inp, "design") as ps:
        assert get_session_id() == str(run_id)
        assert ps._upstream["requirements_payload"] == {"stories": []}
    assert get_session_id() is None


@pytest.mark.asyncio
async def test_the_upstream_read_is_given_the_tenant(no_db):
    """`runs` is FORCE RLS: a read with no `app.tenant_id` GUC returns zero rows rather
    than an error, so an unscoped read here made the Design agent receive no
    requirements at all, silently. The tenant has to reach the read."""
    inp = SimpleNamespace(run_id="abc-123", tenant_id="tenant-9", project_id="p1")
    async with pipeline_session(inp, "design"):
        pass
    assert no_db["read_tenant_id"] == "tenant-9"
