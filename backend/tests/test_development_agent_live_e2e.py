"""Proves the Development agent's actual tool loop end to end, without needing a
live LLM call for the plumbing itself — same technique as
test_security_agent_live_e2e.py / test_documentation_agent_live_e2e.py. Drives the
real compiled dev_agent graph against a real temp git repository (not a mock):
clone -> feature branch -> edit -> lint -> local commit -> push refused without
approval -> push succeeds with approval, with the feature branch genuinely landing
on the origin repo. Plus direct checks on path_guard, sandbox_policy/run_command,
and Task 5's create_ado_repo approval gate.

Every tool call below is real code; only the model's own decisions are scripted.

Notes on how this differs from a naive scripting of the flow:

* ``clone_repo`` only accepts a URL starting with ``http``; anything else is treated
  as a repo *name* that must already be in the session's ``ado_repos`` cache (the
  cache ``list_ado_repos`` populates). So the fixture repo is registered in that
  cache under a name and cloned by name — exactly the production path — rather than
  passed as a ``file://`` URL, which ``clone_repo`` would reject as an unresolvable
  name.
* ``clone_repo`` chooses its own destination (``backend/files/<user>/orchestrator/
  <session>/project``) and overwrites ``session.work_dir`` with it; the test cannot
  pick the workspace, it can only predict it (and clean it up afterwards).
* ``sandbox_policy.validate_command`` only rejects shell *metacharacters* — the
  allowlist-prefix half of the policy lives in ``run_command`` itself (its own
  docstring says so), so both halves are asserted separately here.
"""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
import tempfile
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage

pytestmark = pytest.mark.asyncio

# git_tools._FILES_DIR resolves to <backend>/files — clone_repo always clones there.
_BACKEND_DIR = Path(__file__).resolve().parents[1]
_FILES_DIR = _BACKEND_DIR / "files"

_TEST_USER_ID = "dev-live-e2e-user"


def _force_rmtree(path) -> None:
    """rmtree that survives Windows: git writes its loose objects read-only, and
    plain shutil.rmtree then fails with PermissionError (silently, under
    ignore_errors), leaving whole clone trees behind after every run."""
    def _on_error(func, target, exc):  # noqa: ANN001
        try:
            os.chmod(target, stat.S_IWRITE)
            func(target)
        except Exception:  # noqa: BLE001 — best effort cleanup
            pass

    shutil.rmtree(path, onexc=_on_error)


@pytest.fixture
def local_git_repo():
    """A real local git repo Development can clone_repo() from — no real ADO/GitHub
    connector needed to prove the tool loop itself."""
    src = tempfile.mkdtemp(prefix="dev_agent_live_e2e_src_")
    subprocess.run(["git", "init", "--initial-branch=main"], cwd=src, check=True,
                   capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=src, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=src, check=True)
    (Path(src) / "app.py").write_text("def hello():\n    return 'hi'\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=src, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=src, check=True, capture_output=True)
    yield src
    _force_rmtree(src)


@pytest.fixture
def agent_workspace():
    """Cleans up the workspace tree clone_repo creates under backend/files/."""
    root = _FILES_DIR / _TEST_USER_ID
    _force_rmtree(root)
    yield root
    _force_rmtree(root)


def _resolved_model():
    from shared.services.model_resolver import ResolvedModel

    return ResolvedModel(
        provider="anthropic", litellm_provider="anthropic", model="claude-sonnet-4-6",
        api_key="fake-key-for-client-construction-only", base_url=None, alias="test-alias",
    )


def _next_response(script: list[AIMessage]):
    """Returns a `guarded_completion` side_effect that pops the next canned response
    off `script` on each call. `patch(...)` on an async target (`guarded_completion`
    is `async def`) auto-creates an `AsyncMock`, which — when `side_effect` is a
    plain sync callable, not itself a coroutine function — awaits to that callable's
    RETURN VALUE directly. `guarded_completion`'s own real signature is
    `(resolved, chat_model, messages, *, tenant_id, run_id, agent_type, **kwargs)`
    (`shared/services/model_call_wrapper.py:128-136`) — this side_effect ignores all
    of it and just returns the next scripted AIMessage; the graph, the tool node,
    and every tool the graph calls remain 100% real code.

    Both `resolve_model_for_run` and `guarded_completion` are imported *inside*
    `agent_node` at call time, so they must be patched on their SOURCE modules
    (`shared.services.model_resolver` / `shared.services.model_call_wrapper`), not
    as attributes of `dev_agent`."""
    remaining = list(script)

    def _side_effect(*args, **kwargs):
        return remaining.pop(0)

    return _side_effect


async def test_the_real_tool_loop_clones_edits_lints_commits_and_gates_push(
    local_git_repo, agent_workspace
):
    from agents_orchestrator.development_agent.agents import dev_agent
    from agents_orchestrator.development_agent.config.session_state import (
        clear_session,
        get_session,
    )
    from config.ws_helper import set_session_id, set_user_id

    session_id = f"dev-live-e2e-{uuid.uuid4().hex[:8]}"
    set_session_id(session_id)
    set_user_id(_TEST_USER_ID)
    s = get_session(session_id)

    # clone_repo resolves a non-http repo_url through the ado_repos cache that
    # list_ado_repos would normally have filled in — register the fixture repo there
    # so the agent can clone it by name, exactly as it does in production.
    repo_name = "e2e-fixture-repo"
    s.ado_repos = {repo_name: local_git_repo}
    s.push_gate_enabled = True
    s.push_approved = False  # Turn 1 must NOT push.

    # clone_repo picks this destination itself and writes it back to s.work_dir.
    expected_work_dir = os.path.join(
        str(_FILES_DIR), _TEST_USER_ID, "orchestrator", session_id, "project"
    )

    script = [
        # Turn 1: clone the local repo, branch, edit a file, lint it, commit locally.
        AIMessage(content="", tool_calls=[
            {"name": "clone_repo", "args": {"repo_url": repo_name}, "id": "c1"},
        ]),
        AIMessage(content="", tool_calls=[
            {"name": "create_feature_branch", "args": {"branch_name": "feature/greeting"}, "id": "c2"},
        ]),
        AIMessage(content="", tool_calls=[
            {"name": "edit_file", "args": {
                "relative_path": "app.py",
                "old_string": "return 'hi'",
                "new_string": "return 'hello, world'",
            }, "id": "c3"},
        ]),
        AIMessage(content="", tool_calls=[
            {"name": "lint_and_validate_code", "args": {
                "target_path": os.path.join(expected_work_dir, "app.py"),
                "language": "python",
            }, "id": "c4"},
        ]),
        AIMessage(content="", tool_calls=[
            {"name": "git_commit", "args": {"message": "Update greeting"}, "id": "c5"},
        ]),
        # Turn 1's final attempt: push WITHOUT approval — must be refused.
        AIMessage(content="", tool_calls=[
            {"name": "push_branch", "args": {}, "id": "c6"},
        ]),
        AIMessage(content="Shown the diff, awaiting approval."),
    ]

    config = {"configurable": {"thread_id": session_id}, "recursion_limit": 50}

    with patch("shared.services.model_resolver.resolve_model_for_run") as mock_resolve, \
         patch("shared.services.model_call_wrapper.guarded_completion") as mock_complete:
        mock_resolve.return_value = _resolved_model()
        mock_complete.side_effect = _next_response(script)

        state = {
            "messages": [HumanMessage(content="Update the greeting to say 'hello, world'")],
            "tenant_id": "test-tenant",
            "model_id": None,
        }
        result = await dev_agent.app.ainvoke(state, config=config)

    def _tool_message(name: str):
        msgs = [m for m in result["messages"] if getattr(m, "name", None) == name]
        assert msgs, f"no ToolMessage for {name}"
        return msgs[-1].content

    # The real clone actually happened, into the directory clone_repo chose.
    assert "Successfully cloned to" in _tool_message("clone_repo")
    assert s.work_dir == expected_work_dir
    assert Path(s.work_dir, "app.py").exists()

    # The real edit actually hit the real file on disk.
    assert "Successfully edited" in _tool_message("edit_file")
    assert "hello, world" in Path(s.work_dir, "app.py").read_text(encoding="utf-8")

    # lint_and_validate_code resolved the path through the workspace guard for real.
    # ruff is not installed on every dev box, so the linter's VERDICT is not asserted —
    # only that the tool got past workspace/path resolution and actually tried to run.
    lint_out = _tool_message("lint_and_validate_code")
    assert "no workspace established" not in lint_out
    assert "outside the session workspace" not in lint_out
    assert "path not found" not in lint_out
    if shutil.which("ruff"):
        assert "ruff" in lint_out
    else:
        # No ruff binary: the spawn itself fails, which tool_node surfaces as a tool
        # error. That is still past path resolution, which is what this step proves.
        assert "Tool error" in lint_out

    # Assert the real repo actually has the local commit.
    log = subprocess.run(
        ["git", "log", "--oneline", "-1"], cwd=s.work_dir, capture_output=True, text=True
    )
    assert "Update greeting" in log.stdout

    # Assert push was genuinely refused (not silently skipped) — the tool's own
    # refusal text, including the real diff it computed, reached the transcript.
    push_out = _tool_message("push_branch")
    assert "NOT PUSHED" in push_out
    assert "hello, world" in push_out  # _pending_push_diff really ran against the repo

    # ...and nothing was pushed: the origin has no feature branch yet.
    before = subprocess.run(
        ["git", "branch", "--list", "feature/greeting"],
        cwd=local_git_repo, capture_output=True, text=True,
    )
    assert before.stdout.strip() == ""

    # Turn 2: user approves — push must now succeed against the local origin repo.
    s.push_approved = True
    script2 = [
        AIMessage(content="", tool_calls=[{"name": "push_branch", "args": {}, "id": "c7"}]),
        AIMessage(content="Pushed feature/greeting."),
    ]
    with patch("shared.services.model_resolver.resolve_model_for_run") as mock_resolve2, \
         patch("shared.services.model_call_wrapper.guarded_completion") as mock_complete2:
        mock_resolve2.return_value = _resolved_model()
        mock_complete2.side_effect = _next_response(script2)

        state2 = {
            "messages": [HumanMessage(content="push")],
            "tenant_id": "test-tenant",
            "model_id": None,
        }
        result2 = await dev_agent.app.ainvoke(state2, config=config)

    pushed = [m for m in result2["messages"] if getattr(m, "name", None) == "push_branch"][-1]
    assert "NOT PUSHED" not in pushed.content
    assert "ERROR" not in pushed.content, pushed.content

    # Assert the branch genuinely exists on the "remote" — the original fixture repo,
    # since clone_repo made it push's actual origin.
    branches = subprocess.run(
        ["git", "branch", "--list", "feature/greeting"],
        cwd=local_git_repo, capture_output=True, text=True,
    )
    assert "feature/greeting" in branches.stdout

    # And the pushed commit really carries the edit.
    pushed_file = subprocess.run(
        ["git", "show", "feature/greeting:app.py"],
        cwd=local_git_repo, capture_output=True, text=True,
    )
    assert "hello, world" in pushed_file.stdout

    clear_session(session_id)


async def test_create_ado_repo_is_refused_without_approval():
    """Task 5's consequential-action gate on create_ado_repo, exercised directly."""
    from agents_orchestrator.development_agent.tools.git_tools import create_ado_repo
    from agents_orchestrator.development_agent.config.session_state import (
        clear_session,
        get_session,
    )
    from config.ws_helper import set_session_id

    session_id = f"dev-live-e2e-gate-{uuid.uuid4().hex[:8]}"
    set_session_id(session_id)
    s = get_session(session_id)
    s.push_gate_enabled = True
    s.push_approved = False
    # Credentials are deliberately present so a missing-creds early return cannot be
    # mistaken for the gate firing. The org URL uses the reserved .invalid TLD so that
    # a regression in the gate surfaces as a failed assertion here, never as a real
    # request to a live Azure DevOps organisation.
    s.pat = "fake-pat"
    s.ado_org_url = "https://dev.azure.invalid/example"

    out = await create_ado_repo.ainvoke({"project": "Proj", "repo_name": "new-repo"})
    assert "NOT CREATED" in out
    assert "new-repo" in out

    clear_session(session_id)


async def test_create_pr_is_refused_without_approval():
    """create_pr's HITL gate — the backstop behind push_branch's, structurally
    identical to create_ado_repo's and fired before any network call."""
    from agents_orchestrator.development_agent.tools.git_tools import create_pr
    from agents_orchestrator.development_agent.config.session_state import (
        clear_session,
        get_session,
    )
    from config.ws_helper import set_session_id

    session_id = f"dev-live-e2e-prgate-{uuid.uuid4().hex[:8]}"
    set_session_id(session_id)
    s = get_session(session_id)
    s.push_gate_enabled = True
    s.push_approved = False
    # A repo + branch are deliberately present so the "no repo URL in session" /
    # "no branch name in session" early returns cannot be mistaken for the gate
    # firing. The URL is unroutable (.invalid) so a gate regression fails the
    # assertion locally rather than reaching a live host.
    s.repo_url = "https://dev.azure.invalid/example/Proj/_git/repo"
    s.repo_type = "ado"
    s.branch_name = "feature/greeting"
    s.pat = "fake-pat"

    out = await create_pr.ainvoke({
        "title": "Update greeting",
        "description": "## Summary\nGreeting change.",
    })
    assert "NOT CREATED" in out
    assert "approval" in out

    # The gate really is what stopped it: approve, and the tool proceeds past the gate
    # into its real body. _create_ado_pr rejects the .invalid host at its own URL regex
    # (git_tools.py:992-997) before any httpx call, so this stays offline and
    # deterministic while still proving the refusal above was the gate, not a
    # precondition check.
    s.push_approved = True
    approved_out = await create_pr.ainvoke({
        "title": "Update greeting",
        "description": "## Summary\nGreeting change.",
    })
    assert "NOT CREATED" not in approved_out
    assert "Cannot parse ADO URL" in approved_out

    clear_session(session_id)


async def test_submit_development_artifacts_persists_the_pipeline_handoff():
    """submit_development_artifacts is the Development -> Testing handoff: it rolls the
    session's accumulated artifacts up, computes a final status, and persists both the
    artifacts and the handoff event via patch_session_artifacts.

    patch_session_artifacts is imported at artifact_tools' MODULE level
    (`artifact_tools.py:12`), so it is patched as an attribute of artifact_tools — the
    point of use — not on shared.services.agent_session_store. (Contrast agent_node's
    resolve_model_for_run/guarded_completion, which are function-local imports and so
    must be patched at their source modules.)

    The mock is load-bearing rather than merely convenient: the tool wraps its persist
    call in try/except and returns the same success string either way
    (`artifact_tools.py:105-118`), so asserting on the return value alone would prove
    nothing. The assertions are on what was actually handed to the store."""
    from unittest.mock import AsyncMock

    from agents_orchestrator.development_agent.tools import artifact_tools
    from agents_orchestrator.development_agent.config.session_state import (
        clear_session,
        get_session,
    )
    from config.ws_helper import set_session_id

    session_id = f"dev-live-e2e-submit-{uuid.uuid4().hex[:8]}"
    set_session_id(session_id)
    s = get_session(session_id)

    # State as the earlier tools in the loop would have left it: repo/branch on the
    # session, a changed file recorded by edit_file, and a PR opened by create_pr.
    s.repo_url = "https://dev.azure.invalid/example/Proj/_git/repo"
    s.repo_type = "ado"
    s.branch_name = "feature/greeting"
    s.pr_url = "https://dev.azure.invalid/example/Proj/_git/repo/pullrequest/42"
    s.pr_title = "Update greeting"
    s.dev_artifacts.changed_files.append("app.py")

    with patch.object(artifact_tools, "patch_session_artifacts", new_callable=AsyncMock) as mock_patch:
        out = await artifact_tools.submit_development_artifacts.ainvoke(
            {"batch_id": "BATCH-7", "work_item_ids": ["101", "102"]}
        )

    assert "persisted successfully" in out
    mock_patch.assert_awaited_once()
    call = mock_patch.await_args
    assert call.args[0] == session_id
    assert call.kwargs["tenant_id"] is None

    fields = call.args[1]
    arts = fields["development_artifacts"]

    # Status determination: a PR URL wins over every other signal.
    assert arts["status"] == "pr_created"
    assert s.dev_artifacts.status == "pr_created"

    # Session-level fields the earlier tools set were rolled into the artifact.
    assert arts["repo_url"] == s.repo_url
    assert arts["repo_type"] == "ado"
    assert arts["branch_name"] == "feature/greeting"
    assert arts["pr_url"] == s.pr_url
    assert arts["pr_title"] == "Update greeting"
    assert arts["changed_files"] == ["app.py"]
    # work_item_ids land on the artifact. They are passed as strings because the tool's
    # own schema is Optional[List[str]] — LangChain's arg validation rejects ints at the
    # boundary (confirmed: passing [101, 102] raises a pydantic string_type error before
    # the function body runs), so the body's own str() coercion is belt-and-braces.
    assert arts["work_item_ids"] == ["101", "102"]

    # The handoff event Testing keys off.
    handoff = fields["last_handoff_event"]
    assert handoff["to"] == "testing"
    assert handoff["stage_completed"] == "development"
    assert handoff["batch_id"] == "BATCH-7"
    assert "development_artifacts" in handoff["context_keys"]

    clear_session(session_id)


async def test_submit_development_artifacts_reports_validated_when_there_is_no_pr():
    """The push-only completion path the tool's own docstring calls out: no PR was
    opened, but validation ran — status must be 'validated', not 'pr_created'."""
    from unittest.mock import AsyncMock

    from agents_orchestrator.development_agent.tools import artifact_tools
    from agents_orchestrator.development_agent.config.session_state import (
        clear_session,
        get_session,
    )
    from config.ws_helper import set_session_id
    from shared.models.development import ValidationResult

    session_id = f"dev-live-e2e-submit2-{uuid.uuid4().hex[:8]}"
    set_session_id(session_id)
    s = get_session(session_id)
    s.branch_name = "feature/greeting"
    s.dev_artifacts.changed_files.append("app.py")
    s.dev_artifacts.lint_results.append(
        ValidationResult(name="ruff: app.py", status="passed", command="ruff app.py",
                         summary="No issues", output="")
    )

    with patch.object(artifact_tools, "patch_session_artifacts", new_callable=AsyncMock) as mock_patch:
        await artifact_tools.submit_development_artifacts.ainvoke({})

    arts = mock_patch.await_args.args[1]["development_artifacts"]
    assert arts["pr_url"] is None
    assert arts["status"] == "validated"
    # Default batch_id per the tool's signature.
    assert mock_patch.await_args.args[1]["last_handoff_event"]["batch_id"] == "TEST-READY"

    clear_session(session_id)


async def test_path_guard_blocks_a_traversal_escape(tmp_path):
    from agents_orchestrator.development_agent.tools.path_guard import (
        PathTraversalError,
        resolve_safe_path,
    )

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    # Inside the workspace resolves fine...
    assert resolve_safe_path(str(workspace), "src/app.py").parent.name == "src"

    # ...traversal and root-anchoring do not.
    with pytest.raises(PathTraversalError):
        resolve_safe_path(str(workspace), "../../../etc/passwd")
    assert issubclass(PathTraversalError, ValueError)


async def test_sandbox_policy_blocks_shell_operators_and_run_command_blocks_non_allowlisted():
    from agents_orchestrator.development_agent.tools.git_tools import run_command
    from agents_orchestrator.development_agent.tools.sandbox_policy import validate_command

    # validate_command's contract: None == allowed, a non-None string == refused.
    # It polices shell metacharacters only; the allowlist-prefix half of the policy
    # lives in run_command itself.
    assert validate_command("pytest tests/") is None
    assert validate_command("pytest tests/ && rm -rf /") is not None
    assert validate_command("pytest tests/; rm -rf /") is not None
    assert validate_command("pytest $(whoami)") is not None

    # run_command refuses anything outside its allowlist before touching subprocess.
    assert "not allowed" in run_command.invoke({"command": "rm -rf /"})
    # ...and refuses an allowlisted prefix carrying a shell operator.
    assert "shell operator" in run_command.invoke({"command": "pytest . && rm -rf /"})
