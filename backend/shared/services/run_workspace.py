"""Run-scoped repo workspace for pipeline activities (durable across retries).

A Temporal activity may retry on a different worker, so the workspace is rebuilt
from scratch every call: delete-then-clone into `<DEV_WORKSPACE_ROOT>/run/<run_id>/repo`.
Authenticated cloning REUSES the proven dev-pull-repo helpers in
`shared/services/ado_repos.py` (`clone_into` injects the PAT and scrubs it on error);
this module only adds the optional base-vs-HEAD diff that those helpers don't compute.
"""
from __future__ import annotations

import asyncio
import pathlib
import subprocess
from dataclasses import dataclass, field

from config.env import DEV_WORKSPACE_ROOT
from shared.services import ado_repos


@dataclass
class RunWorkspace:
    work_dir: str
    diff_text: str | None = None
    changed_files: list[str] = field(default_factory=list)


def _run_dir(run_id: str) -> pathlib.Path:
    return pathlib.Path(DEV_WORKSPACE_ROOT) / "run" / str(run_id) / "repo"


def _is_existing_clone(path: pathlib.Path) -> bool:
    """True if *path* already looks like a non-empty git working tree.

    Used to skip re-cloning on every Copilot turn for the same run — the Bridge calls
    `prepare_run_workspace` once per downstream-stage turn, and cloning is the
    expensive, network-bound part `clone_into` would otherwise redo (delete + reclone)
    every time."""
    try:
        return path.is_dir() and (path / ".git").exists() and any(path.iterdir())
    except OSError:
        return False


def _compute_diff(work_dir: str, base: str) -> tuple[str | None, list[str]]:
    """Best-effort `origin/base...HEAD` diff for an existing clone at *work_dir*."""

    def _git(args: list[str], timeout: int = 120) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", *args], cwd=work_dir, capture_output=True, text=True,
            timeout=timeout, encoding="utf-8", errors="replace",
        )

    _git(["fetch", "origin", base])
    rng = f"origin/{base}...HEAD"
    diff = _git(["diff", rng], timeout=180)
    names = _git(["diff", "--name-only", rng])
    diff_text = (diff.stdout or "") or None
    changed = [ln for ln in (names.stdout or "").splitlines() if ln]
    return diff_text, changed


async def prepare_run_workspace(
    run_id: str, repo_url: str, ref: str, base: str | None = None,
    *, pat: str | None = None,
) -> RunWorkspace:
    """Clone *repo_url* at *ref* into the run-scoped dir, idempotently.

    *repo_url* is the raw HTTPS remote (as returned by `ado_repos.resolve_clone_url`);
    auth is injected inside `clone_into`, which deletes any existing clone first so a
    retry on any worker is deterministic. When *pat* is omitted it is resolved from the
    Azure DevOps connector / env via `ado_repos.resolve_auth` (same source the standalone
    `/prepare` paths use). If *base* is given, also computes the diff/changed-files.
    """
    dest_path = _run_dir(run_id)
    dest = str(dest_path)

    if _is_existing_clone(dest_path):
        # Reuse: a prior turn in this run already cloned the workspace. Every Copilot
        # turn re-enters pipeline_session, so without this guard clone_into's
        # delete-then-reclone would run on every single turn.
        pass
    else:
        if pat is None:
            _org, pat = await ado_repos.resolve_auth()
        # clone_into is sync (subprocess git); offload so we don't block the event loop.
        await asyncio.to_thread(ado_repos.clone_into, dest, repo_url, ref, pat or "")

    ws = RunWorkspace(work_dir=dest)
    if base:
        ws.diff_text, ws.changed_files = await asyncio.to_thread(_compute_diff, dest, base)
    return ws
