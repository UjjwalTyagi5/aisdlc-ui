"""cleanup_workspace used to rmtree whatever `work_dir` pointed at.

The highest-priority workspace source is one this agent does NOT create: the
Copilot passes its shared run clone (`ps.work_dir`) — the single checkout that
Code Review, Security, Deployment and Documentation all read for that run —
straight into the testing graph. So running the testing stage deleted the repo
every later stage still needed, and the stage that broke was never the stage
that did it.
"""
import asyncio
import os

from agents_orchestrator.testing_agent.Nodes.finalize import cleanup_workspace


def _run(state):
    return asyncio.run(cleanup_workspace(state))


def test_a_workspace_this_run_created_is_removed(tmp_path):
    wd = tmp_path / "ours"
    wd.mkdir()
    (wd / "f.txt").write_text("x", encoding="utf-8")

    _run({"work_dir": str(wd), "workspace_is_ephemeral": True})

    assert not wd.exists()


def test_the_shared_run_workspace_is_left_alone(tmp_path):
    """The exact pipeline case: the Copilot handed us its clone."""
    wd = tmp_path / "shared-run-clone"
    wd.mkdir()
    (wd / "app.py").write_text("print('later stages need me')", encoding="utf-8")

    _run({"work_dir": str(wd), "workspace_is_ephemeral": False})

    assert wd.exists()
    assert (wd / "app.py").read_text(encoding="utf-8") == "print('later stages need me')"


def test_an_unmarked_caller_supplied_path_is_left_alone(tmp_path):
    """A session checkpointed before the flag existed. Outside the temp root, so
    it cannot have come from this agent's mkdtemp — leave it."""
    wd = tmp_path / "unmarked"
    wd.mkdir()
    (wd / "keep.txt").write_text("keep", encoding="utf-8")

    _run({"work_dir": str(wd)})

    assert wd.exists()


def test_an_unmarked_temp_workspace_is_still_cleaned_up():
    """The same old-checkpoint case for a path this agent did build: every
    workspace it creates comes from mkdtemp, so it lives under the temp root."""
    import tempfile

    wd = tempfile.mkdtemp(prefix="testing_agent_ownership_")
    open(os.path.join(wd, "f.txt"), "w").close()

    _run({"work_dir": wd})

    assert not os.path.exists(wd)


def test_a_missing_work_dir_is_not_an_error():
    _run({"work_dir": "/nonexistent/path/xyz", "workspace_is_ephemeral": True})
