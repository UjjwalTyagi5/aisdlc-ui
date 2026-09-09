"""The repo-reading agents get the run's own checkout.

THE EIGHTH INSTANCE of the pattern `test_run_context_is_complete.py` documents — and
the one that file could not see. Its check reads the wrappers for `ws_helper`
contextvar setters; this state is not a contextvar. It is a `get_prepared()` lookup
followed by direct attribute assignment onto the agent's own session state, so the
guard was blind to it by construction rather than by oversight.

What it cost: Code Review, Security, Documentation and Deployment each resolve their
repository through `s.work_dir`, which only the wrapper filled. Through the
Orchestrator every one of their repo tools returned "no workspace prepared" — while the
Development agent's checkout for that same run sat on disk with 155 files in it.

The failure mode is the dangerous kind. The agents did not stop; they answered from the
conversation, describing code they had never opened, and filed a document that looks
exactly like a real review. `runs.code_review_artifacts` and `runs.security_artifacts`
stayed NULL on the orchestrator runs (c0345c1f) while the standalone runs of the same
week carried a real 6.7 KB unified diff (072633af) and a real SBOM (23914f8b).

Order matters here: the run's OWN Development checkout wins over a target prepared on
the standalone page. In a conversation where Development has just written the code, that
checkout is the thing the user means by "review it", and a stale prepared target from
another day would be reviewed instead — silently, since both produce a confident review.
"""
from __future__ import annotations

import logging
import subprocess

import pytest

from agents_orchestrator.orchestrator2 import workspace


_REPO_READING = ("code_review", "security", "documentation", "deployment")

RUN = "9de55574-d9ca-44c5-8c2f-3b1ad04006f0"
USER = "af57932d-f4f2-4673-aef7-d33b75c022f4"
TENANT = "t-1"
PROJECT = "p-1"


@pytest.fixture(autouse=True)
def _clean_sessions():
    """Each agent's session registry is a module-level dict keyed by session id, and
    every test here uses the same run. Without this the second test reads the first
    one's binding — which is also worth knowing about the real thing: a run's session
    state outlives the turn that created it."""
    yield
    for agent_id in _REPO_READING:
        try:
            workspace._session_state_for(agent_id).clear_session(RUN)
        except Exception:  # noqa: BLE001 - teardown must not mask a failure
            pass


@pytest.fixture
def dev_checkout(tmp_path, monkeypatch):
    """A real git repo where the Development agent's checkout for this run would be."""
    root = tmp_path / "files" / USER / "orchestrator" / RUN / "project"
    root.mkdir(parents=True)
    (root / "README.md").write_text("# QuickLink\n", encoding="utf-8")
    (root / "app.js").write_text("const express = require('express');\n", encoding="utf-8")

    def git(*args):
        subprocess.run(
            ["git", *args], cwd=root, check=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

    git("init", "-b", "main")
    git("config", "user.email", "dev@example.com")
    git("config", "user.name", "Development Agent")
    git("add", ".")
    git("commit", "-m", "feat: scaffold QuickLink")
    git("checkout", "-b", "feature/shortener")
    (root / "routes.js").write_text("router.post('/shorten', shorten);\n", encoding="utf-8")
    git("add", ".")
    git("commit", "-m", "feat: shorten endpoint")
    # A credential in the remote is what the Development agent's clone actually leaves
    # behind — see the PAT embedded in the remotes on this machine.
    git("remote", "add", "origin",
        "https://SECRETPAT123@dev.azure.com/org/Company/_git/quicklink")

    monkeypatch.setattr(workspace, "_FILES_DIR", str(tmp_path / "files"))
    return root


def _session(agent_id: str):
    return workspace._session_state_for(agent_id).get_session(RUN)


def _bind(agent_id: str):
    return workspace.bind(
        agent_id, run_id=RUN, user_id=USER, tenant_id=TENANT, project_id=PROJECT,
    )


# ── the checkout reaches the agent ────────────────────────────────────────────
@pytest.mark.parametrize("agent_id", ["code_review", "security", "documentation", "deployment"])
def test_the_runs_own_checkout_becomes_the_agents_workspace(agent_id, dev_checkout):
    bound = _bind(agent_id)

    assert bound == str(dev_checkout)
    assert _session(agent_id).work_dir == str(dev_checkout)


@pytest.mark.parametrize("agent_id", ["code_review", "security"])
def test_the_agents_own_tools_can_now_read_the_files(agent_id, dev_checkout):
    """The assertion that matters. `work_dir` being set is a means; the tools reading
    a real file is the behaviour, and it is what was broken."""
    _bind(agent_id)

    if agent_id == "code_review":
        from agents_orchestrator.code_review_agent.tools import review_tools as tools
    else:
        from agents_orchestrator.security_agent.tools import security_tools as tools

    from config.ws_helper import set_session_id

    set_session_id(RUN)
    assert tools._work_dir() is not None
    assert (tools._work_dir() / "README.md").read_text(encoding="utf-8") == "# QuickLink\n"


def test_the_git_facts_come_from_the_repo_not_from_a_guess(dev_checkout):
    _bind("code_review")
    s = _session("code_review")

    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=dev_checkout,
        capture_output=True, text=True, check=True,
    ).stdout.strip()

    assert s.head_sha == head
    assert s.source_branch == "feature/shortener"
    assert s.repo_name == "quicklink"
    assert s.mode == "branch"


def test_the_credential_is_stripped_from_the_recorded_remote(dev_checkout):
    """The clone's remote carries the PAT inline. That URL is rendered into the agent's
    context and into the artifact it files, so the token would travel with both."""
    _bind("code_review")
    s = _session("code_review")

    assert "SECRETPAT123" not in s.repo_url
    assert s.repo_url == "https://dev.azure.com/org/Company/_git/quicklink"
    assert s.pat == ""


def test_a_diff_against_the_base_branch_is_computed_for_review(dev_checkout):
    """Code Review's `analyze_diff` takes diff text as an argument; without this the
    agent has a repository and nothing to say about what changed in it."""
    _bind("code_review")
    s = _session("code_review")

    assert "routes.js" in s.diff_text
    assert "shorten" in s.diff_text
    assert s.base_branch == "main"


def test_security_gets_no_diff_because_it_scans_a_tree(dev_checkout):
    """Its session has no diff field at all — scanners read the working tree. Asserted
    so the shared binding cannot grow a field one agent silently ignores."""
    _bind("security")

    assert not hasattr(_session("security"), "diff_text")
    assert _session("security").branch == "feature/shortener"


# ── what it must NOT do ───────────────────────────────────────────────────────
def test_an_agent_that_reads_no_repository_is_left_alone(dev_checkout, monkeypatch, caplog):
    """Requirements, Design, Plan and Development resolve their own directories.
    Development's especially: binding a `work_dir` onto it would point the agent at a
    path it did not choose.

    Returning None is not enough to assert, and a mutant proved it: delete the guard and
    the lookup raises, the fail-soft `except` swallows it, and the answer is still None
    — while every turn of the five agents below logs a traceback. So this checks the
    route taken, not only the value: nothing is looked up, and nothing is logged.
    """
    looked_up: list[str] = []
    real = workspace._session_state_for
    monkeypatch.setattr(
        workspace, "_session_state_for",
        lambda agent_id: (looked_up.append(agent_id), real(agent_id))[1],
    )

    with caplog.at_level(logging.ERROR, logger=workspace.logger.name):
        for agent_id in ("requirements", "design", "plan", "development", "testing"):
            assert workspace.bind(
                agent_id, run_id=RUN, user_id=USER, tenant_id=TENANT, project_id=PROJECT,
            ) is None

    assert looked_up == []
    assert caplog.records == []


def test_nothing_is_bound_when_the_run_has_no_checkout(tmp_path, monkeypatch):
    """A review asked for before any code exists must keep saying so. Inventing an
    empty directory would turn "there is nothing to review" into a clean review."""
    monkeypatch.setattr(workspace, "_FILES_DIR", str(tmp_path / "files"))
    monkeypatch.setattr(workspace, "_prepared_for", lambda *a, **k: None)

    assert _bind("code_review") is None
    assert _session("code_review").work_dir == ""


def test_an_empty_checkout_directory_does_not_count(tmp_path, monkeypatch):
    """`_get_work_dir` in the Development agent calls `makedirs` on every turn, so the
    directory exists as soon as any agent has run — empty. Treating that as a workspace
    would hand Security an empty tree and get back a clean scan."""
    root = tmp_path / "files" / USER / "orchestrator" / RUN / "project"
    root.mkdir(parents=True)
    monkeypatch.setattr(workspace, "_FILES_DIR", str(tmp_path / "files"))
    monkeypatch.setattr(workspace, "_prepared_for", lambda *a, **k: None)

    assert _bind("security") is None


def test_the_runs_own_checkout_wins_over_a_target_prepared_elsewhere(dev_checkout, monkeypatch):
    """Both exist when someone has used the standalone page on this project before. The
    run's own code is what "review what you just built" means."""
    monkeypatch.setattr(
        workspace, "_prepared_for",
        lambda *a, **k: {"work_dir": "/somewhere/else", "repo_name": "older-repo"},
    )

    assert _bind("code_review") == str(dev_checkout)
    assert _session("code_review").repo_name == "quicklink"


def test_a_prepared_target_is_used_when_the_run_built_nothing(tmp_path, monkeypatch):
    """The reverse case: reviewing an existing branch through the Orchestrator without
    a Development turn first. The standalone page's prepared target is then the only
    workspace there is, and it is the same in-memory entry the wrapper reads."""
    monkeypatch.setattr(workspace, "_FILES_DIR", str(tmp_path / "files"))
    prepared = {
        "work_dir": str(tmp_path / "prepared"),
        "repo_name": "quicklink",
        "source_branch": "release/1.2",
        "head_sha": "abc123",
        "mode": "branch",
    }
    (tmp_path / "prepared").mkdir()
    (tmp_path / "prepared" / "f.txt").write_text("x", encoding="utf-8")
    monkeypatch.setattr(workspace, "_prepared_for", lambda *a, **k: prepared)

    assert _bind("code_review") == str(tmp_path / "prepared")
    assert _session("code_review").source_branch == "release/1.2"


def test_binding_never_raises_when_git_is_not_a_repository(tmp_path, monkeypatch):
    """A directory of generated files that was never `git init`ed is a real state — the
    Development agent writes files before it commits. The repo must still be readable;
    only the git facts are missing."""
    root = tmp_path / "files" / USER / "orchestrator" / RUN / "project"
    root.mkdir(parents=True)
    (root / "app.js").write_text("x", encoding="utf-8")
    monkeypatch.setattr(workspace, "_FILES_DIR", str(tmp_path / "files"))

    assert _bind("security") == str(root)
    assert _session("security").head_sha == ""


def test_a_failure_inside_binding_costs_the_turn_nothing(dev_checkout, monkeypatch):
    """Fail-soft, like `dev_artifacts.persist`. A workspace is worth less than the
    user's turn, and this runs before the agent has done any work."""
    monkeypatch.setattr(
        workspace, "_session_state_for",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    assert _bind("code_review") is None


# ── the turn actually calls it ────────────────────────────────────────────────
def test_the_turn_binds_the_workspace():
    """`test_run_context_is_complete.py` cannot see this one: it compares `ws_helper`
    contextvar setters, and this is session-state assignment. Pinned here instead."""
    import inspect

    from agents_orchestrator.orchestrator2 import dispatch

    source = inspect.getsource(dispatch.run_agent)
    assert "workspace.bind(" in source, (
        "a turn never binds the run's checkout, so Code Review, Security, "
        "Documentation and Deployment cannot read the code the run produced"
    )
