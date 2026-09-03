"""Refreshing the clone — PHASE 5.

REAL GIT, not a mock. The whole value of this phase is in what `git` actually does on a
fast-forward versus a rewritten history, and a fake that returns the answers I expect
would test only that I typed them twice.

THE POINT IS NOT THE FETCH. The clone is taken once when a target is prepared and never
refreshed, so a long session can stage a Dockerfile "refresh" against a file somebody
already replaced. What matters is `staged_now_stale`: naming the generated files whose
source moved underneath them, so the PR does not quietly revert somebody's change.
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import tempfile
import uuid

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from agents_orchestrator.deployment_agent.config.session_state import get_session  # noqa: E402
from agents_orchestrator.deployment_agent.tools.deploy_tools import sync_repo  # noqa: E402
from config.ws_helper import set_session_id  # noqa: E402
from shared.services.ado_repos import sync_clone  # noqa: E402

pytestmark = pytest.mark.unit


def _git(cwd, *args, check=True):
    r = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True,
                       timeout=60)
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr}")
    return r.stdout.strip()


@pytest.fixture
def repo():
    """A real bare remote plus a clone of it, on `main`."""
    root = pathlib.Path(tempfile.mkdtemp(prefix="sync-"))
    remote, work, author = root / "remote.git", root / "work", root / "author"
    _git(root, "init", "--bare", "-b", "main", str(remote))

    author.mkdir()
    _git(author, "init", "-b", "main")
    _git(author, "config", "user.email", "t@example.com")
    _git(author, "config", "user.name", "T")
    (author / "app.py").write_text("print(1)\n")
    (author / "Dockerfile").write_text("FROM python:3.12\n")
    _git(author, "add", "-A")
    _git(author, "commit", "-m", "first")
    _git(author, "remote", "add", "origin", str(remote))
    _git(author, "push", "-u", "origin", "main")

    _git(root, "clone", str(remote), str(work))
    _git(work, "config", "user.email", "t@example.com")
    _git(work, "config", "user.name", "T")
    return {"remote": remote, "work": work, "author": author, "root": root}


def _commit(repo, name, body, message="change"):
    (repo["author"] / name).write_text(body)
    _git(repo["author"], "add", "-A")
    _git(repo["author"], "commit", "-m", message)
    _git(repo["author"], "push", "origin", "main")


def _rewrite_after_syncing(repo, work_synced: bool = True):
    """Put the clone on a commit and then make that commit disappear.

    THE CLONE MUST HOLD THE DOOMED COMMIT FIRST. If it is still sitting on the commit
    the author rewinds to, the force-push is a plain fast-forward from its point of
    view — correctly so, and flagging a rewrite there would be a false alarm.
    """
    _commit(repo, "app.py", "print(2)\n")
    if work_synced:
        sync_clone(str(repo["work"]), "main", "")
    _git(repo["author"], "reset", "--hard", "HEAD~1")
    (repo["author"] / "app.py").write_text("print(99)\n")
    _git(repo["author"], "add", "-A")
    _git(repo["author"], "commit", "-m", "rewritten")
    _git(repo["author"], "push", "--force", "origin", "main")


# -- the git layer -------------------------------------------------------------


def test_an_unchanged_branch_reports_no_change(repo):
    out = sync_clone(str(repo["work"]), "main", "")
    assert out["changed"] is False
    assert out["commits"] == 0


def test_new_commits_are_applied_and_counted(repo):
    _commit(repo, "app.py", "print(2)\n")
    _commit(repo, "extra.py", "x = 1\n")
    out = sync_clone(str(repo["work"]), "main", "")
    assert out["changed"] is True
    assert out["commits"] == 2
    assert (repo["work"] / "extra.py").exists()


def test_the_files_that_moved_are_named(repo):
    """A caller has to know which generated files were built on what changed."""
    _commit(repo, "Dockerfile", "FROM python:3.13\n")
    out = sync_clone(str(repo["work"]), "main", "")
    assert "Dockerfile" in out["files"]
    assert "app.py" not in out["files"]


def test_the_working_copy_really_holds_the_new_content(repo):
    """A sync that reports success without changing the files on disk is the worst
    outcome here: everything downstream reads the old code and believes it is new."""
    _commit(repo, "Dockerfile", "FROM python:3.13\n")
    sync_clone(str(repo["work"]), "main", "")
    assert "3.13" in (repo["work"] / "Dockerfile").read_text()


def test_a_rewritten_history_is_not_applied_silently(repo):
    """A force-push means the commit this clone sits on is gone, and anything already
    generated was written against code that no longer exists."""
    _rewrite_after_syncing(repo)

    out = sync_clone(str(repo["work"]), "main", "")
    assert out["history_rewritten"] is True
    assert out["applied"] is False
    assert out["changed"] is False


def test_a_rewritten_history_is_applied_when_accepted(repo):
    _rewrite_after_syncing(repo)

    out = sync_clone(str(repo["work"]), "main", "", accept_rewrite=True)
    assert out["applied"] is True
    assert out["history_rewritten"] is True
    assert "99" in (repo["work"] / "app.py").read_text()


def test_a_missing_clone_is_an_error_not_a_quiet_success(repo):
    with pytest.raises(RuntimeError, match="no clone to sync"):
        sync_clone(str(repo["root"] / "nope"), "main", "")


# -- the tool, and the staleness it exists to report ---------------------------


@pytest.fixture
def session(repo):
    sid = f"sync-{uuid.uuid4().hex[:8]}"
    set_session_id(sid)
    s = get_session(sid)
    s.work_dir = str(repo["work"])
    s.source_branch, s.mode, s.pat = "main", "branch", ""
    s.staged_files = []
    return s


async def test_it_names_staged_files_whose_source_moved(repo, session):
    """THE POINT OF THE PHASE. A generated file written against a base that has since
    changed quietly reverts whatever moved underneath it."""
    session.staged_files = [
        {"path": "Dockerfile", "contents": "FROM python:3.12\n", "language": "docker"},
        {"path": "deploy/deployment.yaml", "contents": "kind: Deployment\n",
         "language": "yaml"},
    ]
    _commit(repo, "Dockerfile", "FROM python:3.13\n")
    out = json.loads(await sync_repo.ainvoke({}))
    assert out["staged_now_stale"] == ["Dockerfile"]
    assert "re-stage" in out["detail"].lower()


async def test_untouched_staged_files_are_not_cried_wolf_over(repo, session):
    session.staged_files = [
        {"path": "deploy/deployment.yaml", "contents": "kind: Deployment\n"}]
    _commit(repo, "app.py", "print(2)\n")
    out = json.loads(await sync_repo.ainvoke({}))
    assert out["staged_now_stale"] == []


async def test_it_still_warns_that_the_assessment_predates_the_change(repo, session):
    """No stale FILE does not mean nothing changed — the readiness call was made
    against older code."""
    _commit(repo, "app.py", "print(2)\n")
    out = json.loads(await sync_repo.ainvoke({}))
    assert "older code" in out["detail"]


async def test_an_up_to_date_repo_says_so_plainly(repo, session):
    out = json.loads(await sync_repo.ainvoke({}))
    assert out["synced"] is True and out["changed"] is False


async def test_the_head_the_session_tracks_moves_with_it(repo, session):
    _commit(repo, "app.py", "print(2)\n")
    out = json.loads(await sync_repo.ainvoke({}))
    assert session.head_sha == out["after"]


async def test_a_rewritten_history_asks_before_discarding_anything(repo, session):
    _rewrite_after_syncing(repo)

    out = json.loads(await sync_repo.ainvoke({}))
    assert out["synced"] is False
    assert out["history_rewritten"] is True
    assert "ask the user" in out["detail"].lower()


async def test_a_pr_session_is_not_dragged_off_the_commit_being_assessed(repo, session):
    """Syncing a PR-bound session to the branch tip would silently change WHAT is being
    assessed, which is the one thing the reviewer thinks is fixed."""
    session.mode = "pr"
    out = json.loads(await sync_repo.ainvoke({}))
    assert out["synced"] is False
    assert "pull request" in out["detail"]


async def test_an_unprepared_session_says_so(repo, session):
    session.work_dir = ""
    assert "no workspace prepared" in await sync_repo.ainvoke({})


async def test_a_sync_failure_admits_the_copy_is_still_stale(repo, session):
    """"Could not sync" alone reads as harmless. It is not: everything downstream is
    still reading old code."""
    session.source_branch = "no-such-branch"
    out = json.loads(await sync_repo.ainvoke({}))
    assert out["synced"] is False
    assert "still as stale" in out["detail"]


def test_a_force_push_the_clone_never_saw_is_not_a_rewrite(repo):
    """A clone still sitting on the commit the author rewound TO sees a plain
    fast-forward, and calling that a rewritten history would be a false alarm that
    makes somebody discard good work.

    I wrote the tests above wrong first — pushing the rewrite without the clone ever
    holding the doomed commit — which is exactly how this distinction earned its own
    case.
    """
    _rewrite_after_syncing(repo, work_synced=False)
    out = sync_clone(str(repo["work"]), "main", "")
    assert out["history_rewritten"] is False
    assert out["changed"] is True
