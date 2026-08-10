"""Task 5 / C2 — _seed_downstream_prepared feeds Code Review/Security/Deployment/
Documentation from the Bridge's shared run clone (`ps.work_dir` / `ps._workspace`)
instead of requiring the standalone `/prepare` REST call the Copilot never makes.

C2 fix: the (tenant, project)-keyed `_prepared` store is only bridged into an
agent's session by its STANDALONE `*_api.py` WS handler, which the Copilot never
calls (it invokes the graph directly). The graph's own TOOLS read
`get_session(get_session_id())`, where `get_session_id()` resolves to the pipeline
`run_id` — so `_seed_downstream_prepared` must ALSO seed the session object keyed
by `run_id`, or the seeded work_dir never reaches the tools."""
from types import SimpleNamespace

from agents_orchestrator.orchestrator import copilot_api


def _fake_ps(work_dir="/tmp/x", diff_text="d", changed_files=None):
    changed_files = ["a"] if changed_files is None else changed_files
    return SimpleNamespace(
        work_dir=work_dir,
        _workspace=SimpleNamespace(diff_text=diff_text, changed_files=changed_files),
    )


class _FakeSession:
    """Stand-in for a *SessionState dataclass instance — plain attribute bag."""
    def __init__(self):
        self.work_dir = ""
        self.diff_text = ""
        self.changed_files = []
        self.project_id = ""


def test_seeds_code_review_with_work_dir_diff_and_changed_files(monkeypatch):
    calls = []
    sessions = {}
    import agents_orchestrator.code_review_agent.config.session_state as cr_state
    monkeypatch.setattr(cr_state, "set_prepared", lambda t, p, data: calls.append((t, p, data)))
    monkeypatch.setattr(cr_state, "get_session", lambda sid: sessions.setdefault(sid, _FakeSession()))

    copilot_api._seed_downstream_prepared("code_review", "t1", "p1", "run-1", _fake_ps())

    assert len(calls) == 1
    tenant_id, project_id, data = calls[0]
    assert (tenant_id, project_id) == ("t1", "p1")
    assert data["work_dir"] == "/tmp/x"
    assert data["diff_text"] == "d"
    assert data["changed_files"] == ["a"]

    session = sessions["run-1"]
    assert session.work_dir == "/tmp/x"
    assert session.diff_text == "d"
    assert session.changed_files == ["a"]
    assert session.project_id == "p1"


def test_seeds_security_with_work_dir(monkeypatch):
    calls = []
    sessions = {}
    import agents_orchestrator.security_agent.config.session_state as sec_state
    monkeypatch.setattr(sec_state, "set_prepared", lambda t, p, data: calls.append((t, p, data)))
    monkeypatch.setattr(sec_state, "get_session", lambda sid: sessions.setdefault(sid, _FakeSession()))

    copilot_api._seed_downstream_prepared("security", "t1", "p1", "run-1", _fake_ps())

    assert len(calls) == 1
    tenant_id, project_id, data = calls[0]
    assert (tenant_id, project_id) == ("t1", "p1")
    assert data["work_dir"] == "/tmp/x"
    assert sessions["run-1"].work_dir == "/tmp/x"
    assert sessions["run-1"].project_id == "p1"


def test_seeds_deployment_with_work_dir(monkeypatch):
    calls = []
    sessions = {}
    import agents_orchestrator.deployment_agent.config.session_state as dep_state
    monkeypatch.setattr(dep_state, "set_prepared", lambda t, p, data: calls.append((t, p, data)))
    monkeypatch.setattr(dep_state, "get_session", lambda sid: sessions.setdefault(sid, _FakeSession()))

    copilot_api._seed_downstream_prepared("deployment", "t1", "p1", "run-1", _fake_ps())

    assert len(calls) == 1
    assert calls[0][2]["work_dir"] == "/tmp/x"
    assert sessions["run-1"].work_dir == "/tmp/x"
    assert sessions["run-1"].project_id == "p1"


def test_seeds_documentation_with_work_dir(monkeypatch):
    calls = []
    sessions = {}
    import agents_orchestrator.documentation_agent.config.session_state as doc_state
    monkeypatch.setattr(doc_state, "set_prepared", lambda t, p, data: calls.append((t, p, data)))
    monkeypatch.setattr(doc_state, "get_session", lambda sid: sessions.setdefault(sid, _FakeSession()))

    copilot_api._seed_downstream_prepared("documentation", "t1", "p1", "run-1", _fake_ps())

    assert len(calls) == 1
    assert calls[0][2]["work_dir"] == "/tmp/x"
    assert sessions["run-1"].work_dir == "/tmp/x"
    assert sessions["run-1"].project_id == "p1"


def test_seeds_documentation_project_id_fixes_c2_output_dir_mismatch(monkeypatch):
    """C2 regression: the Documentation writer (`doc_tools._output_dir`) keys its
    output directory off `session.project_id`, while the reader
    (`runs._run_stage_output_dir`) keys off `run.project_id`. Before the fix,
    `_seed_downstream_prepared` never set `s.project_id`, so it stayed "" and docs
    always wrote to a directory the reader never looked in."""
    sessions = {}
    import agents_orchestrator.documentation_agent.config.session_state as doc_state
    monkeypatch.setattr(doc_state, "set_prepared", lambda t, p, data: None)
    monkeypatch.setattr(doc_state, "get_session", lambda sid: sessions.setdefault(sid, _FakeSession()))

    real_project_id = "11111111-2222-3333-4444-555555555555"
    copilot_api._seed_downstream_prepared("documentation", "t1", real_project_id, "run-1", _fake_ps())

    assert sessions["run-1"].project_id == real_project_id


def test_no_work_dir_seeds_nothing(monkeypatch):
    """A ps with work_dir=None (Bridge had no repo to clone / clone failed) must not
    call any downstream agent's set_prepared or touch its session."""
    calls = []
    import agents_orchestrator.code_review_agent.config.session_state as cr_state
    import agents_orchestrator.security_agent.config.session_state as sec_state
    import agents_orchestrator.deployment_agent.config.session_state as dep_state
    import agents_orchestrator.documentation_agent.config.session_state as doc_state
    for mod in (cr_state, sec_state, dep_state, doc_state):
        monkeypatch.setattr(mod, "set_prepared", lambda t, p, data: calls.append((t, p, data)))
        monkeypatch.setattr(mod, "get_session", lambda sid: calls.append(("get_session", sid)))

    ps = _fake_ps(work_dir=None)
    for stage in ("code_review", "security", "deployment", "documentation"):
        copilot_api._seed_downstream_prepared(stage, "t1", "p1", "run-1", ps)

    assert calls == []


def test_unrelated_stage_seeds_nothing(monkeypatch):
    """requirements/design/development/testing don't use a prepared-store; the
    function must be a no-op for them even with a valid work_dir."""
    calls = []
    import agents_orchestrator.code_review_agent.config.session_state as cr_state
    monkeypatch.setattr(cr_state, "set_prepared", lambda t, p, data: calls.append((t, p, data)))

    for stage in ("requirements", "design", "development", "testing"):
        copilot_api._seed_downstream_prepared(stage, "t1", "p1", "run-1", _fake_ps())

    assert calls == []


def test_failing_agent_module_does_not_block_others(monkeypatch):
    """Fail-soft: if one agent's set_prepared blows up, seeding must still not raise —
    the turn loop calls this once per stage per turn, so only the ACTIVE stage's
    setter runs per call; a single call must never propagate an exception."""
    import agents_orchestrator.security_agent.config.session_state as sec_state

    def _boom(t, p, data):
        raise RuntimeError("boom")

    monkeypatch.setattr(sec_state, "set_prepared", _boom)

    # Must not raise.
    copilot_api._seed_downstream_prepared("security", "t1", "p1", "run-1", _fake_ps())
