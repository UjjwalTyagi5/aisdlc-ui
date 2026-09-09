"""Give the repo-reading agents the run's own checkout.

Code Review, Security, Documentation and Deployment do not resolve a working directory
from the run the way the Development agent does. Each reads `s.work_dir` off its own
session state, and that field is filled by one `get_prepared()` call living in the
agent's `*_agent_api.py` wrapper. This engine loads each agent's graph and never that
wrapper (D10a), so through the Orchestrator the field stayed empty and every repo tool
answered "no workspace prepared" — with the run's own Development checkout sitting on
disk beside it.

The damage was not a visible failure. The agents answered from the conversation instead,
describing code they had never opened, and filed a document indistinguishable from a
real review. `runs.code_review_artifacts` and `runs.security_artifacts` stayed NULL on
the Orchestrator runs while standalone runs of the same week carried a real unified diff
and a real SBOM.

This is the eighth instance of the pattern `tests/orchestrator2/
test_run_context_is_complete.py` was written to end, and the one it could not catch: it
compares the `ws_helper` contextvar setters the wrappers use, and this state is not a
contextvar. `tests/orchestrator2/test_workspace_binding.py` covers it directly.

Two sources, in this order:

1. **The run's own Development checkout.** In a conversation where Development has just
   written the code, this is what the user means by "now review it".
2. **A target prepared on the standalone page**, the same entry keyed by
   (tenant, project) that the wrapper reads — so reviewing an existing branch through
   the Orchestrator, with no Development turn, still works. It is no longer only in
   memory: `get_prepared` now falls back to the record written beside the checkout, so
   a target survives the restart that cloning it used to cause.

Nothing is invented. With no checkout and nothing prepared, this binds nothing and the
agent goes on saying it has no workspace, which is the truth and is far better than a
clean review of an empty directory.
"""
from __future__ import annotations

import logging
import os
import subprocess
from typing import Any
from urllib.parse import urlsplit, urlunsplit

logger = logging.getLogger(__name__)

#: Mirrors the Development agent's own `_get_work_dir`, whose path this reads.
_FILES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "files")

#: The agents whose tools read `s.work_dir`. Requirements, Design, Plan and Testing
#: resolve their own output directories and have no such field; DEVELOPMENT is excluded
#: deliberately — it derives its directory from the run itself, which is exactly why its
#: code tree already appears, and writing one onto it would point the agent at a path it
#: did not choose.
_REPO_READING_AGENTS: dict[str, str] = {
    "code_review": "agents_orchestrator.code_review_agent.config.session_state",
    "security": "agents_orchestrator.security_agent.config.session_state",
    "documentation": "agents_orchestrator.documentation_agent.config.session_state",
    "deployment": "agents_orchestrator.deployment_agent.config.session_state",
}

#: Their branch field is not spelled the same way. Security calls it `branch`; the other
#: three call it `source_branch`. Setting only one leaves the other agent reporting the
#: repository's default branch whatever it was actually given.
_BRANCH_FIELD = {
    "code_review": "source_branch",
    "security": "branch",
    "documentation": "source_branch",
    "deployment": "source_branch",
}

_GIT_TIMEOUT = 20


def _session_state_for(agent_id: str) -> Any:
    """The agent's own session-state module — imported lazily, like the registry, so
    binding for one agent never drags in the other three."""
    import importlib

    return importlib.import_module(_REPO_READING_AGENTS[agent_id])


def _prepared_for(agent_id: str, tenant_id: str, project_id: str | None) -> dict | None:
    """The standalone page's prepared target for this project, if there is one."""
    if not project_id:
        return None
    try:
        return _session_state_for(agent_id).get_prepared(tenant_id, project_id)
    except Exception:  # noqa: BLE001 - a fallback source, never fatal
        return None


def _git(work_dir: str, *args: str) -> str:
    """One git command, or "" — a checkout with no commits yet, or none at all, is a
    real state the Development agent passes through between writing and committing."""
    try:
        out = subprocess.run(
            ["git", *args], cwd=work_dir, capture_output=True, text=True,
            timeout=_GIT_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout.strip() if out.returncode == 0 else ""


def _without_credentials(url: str) -> str:
    """Strip any userinfo from a remote URL.

    The Development agent's clone embeds the PAT inline
    (`https://<token>@dev.azure.com/...`), and this URL is rendered into the agent's
    context and stored on the artifact it files — so the token would travel with both.
    """
    if not url:
        return ""
    try:
        parts = urlsplit(url)
    except ValueError:
        return ""
    if not parts.hostname:
        return url
    host = parts.hostname + (f":{parts.port}" if parts.port else "")
    return urlunsplit((parts.scheme, host, parts.path, parts.query, parts.fragment))


def _has_files(path: str) -> bool:
    """A directory with nothing in it is not a workspace.

    The Development agent's `_get_work_dir` calls `makedirs` on every turn, so the path
    exists the moment any agent has run. Accepting it would hand Security an empty tree
    and get back a clean scan — a pass that means nothing was there to fail.
    """
    try:
        for _, dirs, files in os.walk(path):
            dirs[:] = [d for d in dirs if d != ".git"]
            if files:
                return True
    except OSError:
        return False
    return False


def _run_checkout(user_id: str, run_id: str) -> str | None:
    """Where the Development agent works, on this run. Kept in step with
    `development_agent/tools/file_tools.py::_get_work_dir` and `runs.py::
    _run_dev_work_dir`, which build the same path."""
    path = os.path.join(_FILES_DIR, str(user_id), "orchestrator", str(run_id), "project")
    return path if os.path.isdir(path) and _has_files(path) else None


def _facts_from_git(work_dir: str) -> dict:
    """What the repository says about itself. Read, never guessed — a head SHA or a
    branch name invented here would be filed on the artifact as evidence."""
    branch = _git(work_dir, "rev-parse", "--abbrev-ref", "HEAD")
    remote = _without_credentials(_git(work_dir, "remote", "get-url", "origin"))
    facts = {
        "work_dir": work_dir,
        "mode": "branch",
        "head_sha": _git(work_dir, "rev-parse", "HEAD"),
        "branch": branch,
        "repo_url": remote,
        # The clone's own folder name is a guess; the remote's last segment is what the
        # repository is actually called.
        "repo_name": remote.rstrip("/").rsplit("/", 1)[-1] if remote else "",
        # DELIBERATELY EMPTY. The tools take a PAT to clone with, and this workspace is
        # already on disk — carrying the token no further than it has to go.
        "pat": "",
    }
    return facts


def _diff_against_base(work_dir: str, branch: str) -> tuple[str, str]:
    """(diff_text, base_branch) for Code Review, best-effort.

    `analyze_diff` takes the diff as an argument rather than reading the repo, so
    without this the agent has a checkout and nothing to say about what changed in it.
    Tries the usual default branches; a repo on its first branch simply has no base, and
    an empty diff is the honest answer rather than a diff against nothing.
    """
    for base in ("main", "master", "develop"):
        if base == branch:
            continue
        if not _git(work_dir, "rev-parse", "--verify", base):
            continue
        diff = _git(work_dir, "diff", f"{base}...HEAD")
        if diff:
            return diff, base
    return "", ""


def bind(
    agent_id: str,
    *,
    run_id: str,
    user_id: str,
    tenant_id: str,
    project_id: str | None,
) -> str | None:
    """Bind a workspace onto `agent_id`'s session state for this run.

    Returns the directory bound, or None when this agent reads no repository, when
    there is nothing to bind, or when binding failed.

    NEVER RAISES. This runs before the agent has done any work, and a missing workspace
    is worth less than the user's turn — the same judgement `dev_artifacts.persist`
    makes on the other side of it.
    """
    if agent_id not in _REPO_READING_AGENTS:
        return None

    try:
        state = _session_state_for(agent_id)

        work_dir = _run_checkout(user_id, run_id)
        if work_dir:
            facts = _facts_from_git(work_dir)
        else:
            prepared = _prepared_for(agent_id, tenant_id, project_id)
            if not prepared or not prepared.get("work_dir"):
                return None
            facts = dict(prepared)
            work_dir = str(facts["work_dir"])

        session = state.get_session(run_id)
        branch = facts.pop("branch", "") or facts.pop(_BRANCH_FIELD[agent_id], "")

        for key, value in facts.items():
            if hasattr(session, key):
                setattr(session, key, value)
        if branch:
            setattr(session, _BRANCH_FIELD[agent_id], branch)

        session.tenant_id = tenant_id
        session.project_id = str(project_id or "")

        # Only Code Review has somewhere to put a diff; Security and Deployment scan the
        # tree, and Documentation describes it.
        if hasattr(session, "diff_text") and not session.diff_text:
            diff, base = _diff_against_base(work_dir, branch)
            if diff:
                session.diff_text = diff
                if hasattr(session, "base_branch"):
                    session.base_branch = base

        # What the wrapper's own binding sets once it has copied the target across, so
        # nothing downstream tries to bind a second time.
        session.target_bound = True
        return work_dir
    except Exception:  # noqa: BLE001 - a workspace must never cost a turn
        logger.exception(
            "orchestrator2 could not bind a workspace (agent=%s run=%s)", agent_id, run_id
        )
        return None
