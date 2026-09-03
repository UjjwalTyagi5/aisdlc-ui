"""Workspace setup nodes — full-zip and single-file paths.

Phase 4a — extracted from `super_agent.py` IntentDrivenAgent methods at:
- setup_workspace               (line 333)
- setup_single_file_workspace   (line 347)
- _extract_zip                  (line 378)
"""
from __future__ import annotations

import asyncio
import os
import pathlib
import shutil
import subprocess
import tempfile
import zipfile
from typing import Optional

from agents_orchestrator.testing_agent.config.session_state import SuperAgentState
from agents_orchestrator.testing_agent.config.shared import blog, get_session_id, get_user_id, logger


def _extract_zip(zip_path: str, work_dir: str) -> None:
    """Helper for zip extraction (runs in executor).

    Phase 8.3 — defensive against path-traversal zip-slip attacks. A malicious
    zip with members like `../../etc/passwd` would, with the prior
    `extractall(work_dir)` call, escape work_dir and overwrite system files.
    Now we resolve each member's destination and skip (with a warning) any
    that resolves outside `work_dir`.
    """
    target_root = os.path.realpath(work_dir)
    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
        for member in zip_ref.namelist():
            dest = os.path.realpath(os.path.join(work_dir, member))
            # `dest` must be at or below `target_root`
            if dest != target_root and not dest.startswith(target_root + os.sep):
                logger.warning(
                    f"_extract_zip: rejecting unsafe member {member!r} "
                    f"(would escape work_dir → {dest})"
                )
                continue
            zip_ref.extract(member, work_dir)


async def setup_workspace(state: SuperAgentState):
    # Task 5 — an already-prepared work_dir (from the Copilot's shared run workspace,
    # i.e. the Pipeline Session Bridge's `ps.work_dir`) wins over everything else: reuse
    # it directly instead of re-cloning. Guards against a stale/deleted path so a bad
    # value falls through cleanly to the clone_target/upstream_development fallbacks
    # below rather than handing an empty dir to the analysis step.
    prepared_work_dir = state.get("work_dir")
    if isinstance(prepared_work_dir, str) and prepared_work_dir and os.path.isdir(prepared_work_dir):
        logger.info(f"setup_workspace: reusing prepared work_dir {prepared_work_dir}")
        blog("Reusing the shared run workspace (already cloned for this run)")
        return {"work_dir": prepared_work_dir}

    # Phase B.1 — explicit clone_target wins over everything else.
    clone_target = state.get("clone_target")
    if isinstance(clone_target, dict) and clone_target.get("repo") and clone_target.get("branch"):
        # Phase 8.8b — when natural-language parsing didn't extract a project
        # ("test the X branch in carelon repo" gives no project), default
        # project=repo. ADO convention `<project>/<project>/_git/<repo>` is
        # very common (e.g. carelon/carelon, aicore/aicore). Falls through
        # cleanly to a friendly error if the resulting project is wrong —
        # user can re-issue with explicit "in project Y".
        project = clone_target.get("project") or clone_target["repo"]
        return await _clone_into_workspace(
            project=project,
            repo=clone_target["repo"],
            branch=clone_target["branch"],
            tenant_id=state.get("tenant_id") or "",
            project_id=state.get("project_id") or "",
            owner_id=state.get("owner_id") or "",
        )

    # Phase 5 — fall back to upstream development_artifacts.repo_url if dev agent
    # left one. clone_target overrides; this is the orchestrator-driven path.
    upstream_dev = state.get("upstream_development") or {}
    if isinstance(upstream_dev, dict) and upstream_dev.get("repo_url"):
        repo_url = upstream_dev.get("repo_url")
        branch = upstream_dev.get("branch_name") or "main"
        parsed = _parse_ado_repo_url(repo_url)
        if parsed:
            project, repo = parsed
            logger.info(f"Phase 5: cloning upstream dev repo {project}/{repo}@{branch}")
            return await _clone_into_workspace(
                project=project, repo=repo, branch=branch,
                tenant_id=state.get("tenant_id") or "",
                project_id=state.get("project_id") or "",
                owner_id=state.get("owner_id") or "",
            )
        else:
            logger.warning(f"upstream repo_url is not an ADO URL ({repo_url!r}); falling through to upload path")

    # Default — upload path (Phase 0/2 behaviour).
    selected_types = set(state.get("selected_test_types") or [])
    if selected_types and "unit" not in selected_types and not state.get("input_file_path"):
        work_dir = tempfile.mkdtemp(prefix="testing_agent_api_")
        blog("Prepared lightweight workspace for API testing")
        return {"work_dir": work_dir}

    logger.info(f"Setting up workspace and unzipping {state['input_file_path']}")
    blog("Setting up workspace and extracting files...")

    work_dir = tempfile.mkdtemp()
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _extract_zip, state['input_file_path'], work_dir)

    logger.info(f"Code unzipped to: {work_dir}")
    blog("Files extracted to workspace")
    return {"work_dir": work_dir}


def _find_dev_workspace_path(sid: Optional[str], uid: Optional[str]) -> Optional[pathlib.Path]:
    """Post-MVP Phase 5 — locate the development agent's local clone for the
    current session, if it exists.

    Dev's `git_tools.py` clones to:
      `<agentic_app>/files/<user_id>/orchestrator/<session_id>/project`
    where `<agentic_app>` is `parents[3]` from dev's git_tools.py file.

    From this file (`Nodes/workspace.py`) the agentic_app root is also
    `parents[3]`. Returns the path if it exists, else None.

    `sid` and `uid` are passed as args (not read from contextvars here)
    because this function runs inside `loop.run_in_executor()` — Python
    3.7+ asyncio's default executor copies the contextvars context, but
    relying on that is fragile across runtimes. Passing them explicitly
    is safer.

    Defensive: if the path convention ever changes on dev's side, this
    returns None and the caller falls back to a remote clone — no breakage.
    """
    if not sid or not uid or sid == "default_session":
        return None
    agentic_app = pathlib.Path(__file__).resolve().parents[3]
    candidates = [
        agentic_app / "files" / uid / "orchestrator" / sid / "project",
        agentic_app / "agents_orchestrator" / "development_agent" / "files" / uid / "orchestrator" / sid / "project",
    ]
    for path in candidates:
        if path.is_dir() and (path / ".git").exists():
            return path
    return None


def _build_ado_remote_url(org_url: str, project: str, repo: str, pat: str) -> Optional[str]:
    """Phase 8.1 — construct the same ADO HTTPS URL that ado_clone.clone_branch
    builds, so we can `git remote add` it onto a local-reused clone and fetch
    the base branch. PAT is URL-encoded; matches existing pattern used in
    ado_clone.py."""
    from urllib.parse import quote
    if not org_url or not repo:
        return None
    org = org_url.rstrip("/")
    if not org.startswith(("http://", "https://")):
        return None
    project_seg = f"/{project}" if project else ""
    return f"https://anything:{quote(pat)}@{org.split('://', 1)[1]}{project_seg}/_git/{repo}"


def _add_ado_remote_and_fetch_main(work_dir: str, ado_url: str) -> bool:
    """Phase 8.1 — add ADO as a remote called `ado` and fetch main into
    `origin/main` so Phase 7's PR-scoped diff works on local-reused clones.

    Uses `origin/main` ref (not `ado/main`) to keep pr_diff.py callers
    unchanged — they look for origin/<base>.
    """
    try:
        # Add (or update) the remote
        rc, _, _ = _run_git_local(["remote", "add", "ado", ado_url], work_dir, timeout_s=10)
        if rc != 0:
            # Already exists — update its URL
            _run_git_local(["remote", "set-url", "ado", ado_url], work_dir, timeout_s=10)
        # Fetch main from ADO into the origin/main ref so existing pr_diff
        # code (which looks for `origin/<base>`) finds it.
        rc, _, err = _run_git_local(
            ["fetch", "--depth=200", "ado", "main:refs/remotes/origin/main"],
            work_dir, timeout_s=120,
        )
        if rc != 0:
            logger.warning(f"_add_ado_remote_and_fetch_main: fetch failed: {(err or '')[:200]}")
            return False
        return True
    except Exception as exc:
        logger.warning(f"_add_ado_remote_and_fetch_main raised: {exc}")
        return False


def _run_git_local(args: list, cwd: str, timeout_s: int = 30) -> tuple:
    """Tiny subprocess wrapper used by 8.1's ADO-remote-add helpers (avoids
    importing pr_diff._run_git here since we use it before pr_diff is even
    imported in the caller)."""
    try:
        r = subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True,
            timeout=timeout_s, check=False,
        )
        return r.returncode, r.stdout or "", r.stderr or ""
    except Exception as exc:
        return 1, "", str(exc)


def _local_clone_from_dev(branch: str, dest_dir: str, sid: Optional[str], uid: Optional[str]) -> bool:
    """Post-MVP Phase 5 — try `git clone --local` from dev's already-cloned
    workspace. Avoids a network round-trip to ADO when dev just finished.

    Returns True on success, False otherwise. The caller must fall back to
    a normal remote clone on failure.

    `git clone --local` uses hardlinks where possible, so it's fast
    (seconds even for large repos) and isolates the clone in `dest_dir`
    so testing-agent writes don't pollute dev's working tree.

    `sid` / `uid` passed explicitly so this function works reliably when
    called via `loop.run_in_executor()` (avoids depending on contextvar
    propagation into worker threads).
    """
    src = _find_dev_workspace_path(sid, uid)
    if src is None:
        return False
    try:
        result = subprocess.run(
            ["git", "clone", "--local", "--branch", branch, str(src), dest_dir],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode == 0:
            logger.info(f"Reused dev workspace via `git clone --local` from {src}")
            return True
        # The branch might not exist locally in dev's clone (e.g. dev only
        # pushed it to remote). Fall back to remote clone.
        stderr_head = (result.stderr or "")[:200]
        logger.info(f"git clone --local from dev workspace failed (will fall back to remote): {stderr_head}")
        # Clean up any partial dest from the failed clone so the remote-clone
        # retry can use the same dest_dir.
        if os.path.isdir(dest_dir):
            shutil.rmtree(dest_dir, ignore_errors=True)
            os.makedirs(dest_dir, exist_ok=True)
        return False
    except Exception as exc:
        logger.warning(f"git clone --local raised {exc} — falling back to remote clone")
        return False


async def _clone_into_workspace(
    project: str, repo: str, branch: str, tenant_id: str = "",
    project_id: str = "", owner_id: str = "",
) -> dict:
    """Phase B.1 — clone an ADO branch into a fresh tmpdir.

    Returns a partial state dict with `work_dir` (and `repo_workspace` for
    parity with Phase 5 plumbing).

    Post-MVP Phase 1 — on connector-missing OR clone failure, returns a
    graceful state with an empty `work_dir` and a user-facing
    `clone_error_message` so the graph routes through handle_analysis_failure
    → package_final_reports instead of raising 500. The downstream
    find_and_analyze_code finds 0 functions in the empty dir → routing's
    decide_code_analysis_outcome returns "analysis_failed" → the friendly
    error reaches the user via the final summary.
    """
    from agents_orchestrator.testing_agent.tools.ado_clone import clone_branch

    # Post-MVP Phase 5 — workspace reuse optimization. If the development agent
    # already cloned this repo+branch into its local workspace (typical when the
    # orchestrator just ran dev → testing on the same FastAPI worker), try a
    # `git clone --local` from that path. Saves the network round-trip to ADO
    # and works without any ADO connector — useful on machines that lack creds.
    fast_dest = tempfile.mkdtemp(prefix="testing_agent_local_")
    loop = asyncio.get_running_loop()
    # Read contextvars HERE (in the async event-loop thread) and pass into the
    # worker explicitly. Don't rely on PEP 567 contextvar propagation through
    # the executor — it works in CPython 3.7+ but is implementation-dependent.
    sid = get_session_id()
    uid = get_user_id()
    if await loop.run_in_executor(None, _local_clone_from_dev, branch, fast_dest, sid, uid):
        blog(f"Reused development agent's local workspace (via git clone --local @ branch {branch})")
        # Phase 8.1 — local-reuse clone's `origin` points at dev's local path,
        # which only has the PR branch (dev cloned --depth 1). PR-scoped diff
        # needs the base branch (main) too — add ADO as a separate remote and
        # fetch base from there. Best-effort; failure just means PR-scoped
        # filter + coverage skip on this path (analyze.py keeps full analysis
        # on empty diff, so no regression vs pre-Phase-7 behaviour).
        try:
            from agents_orchestrator.testing_agent.tools.pr_diff import fetch_base_branch
            from shared.services.ado_repos import resolve_auth

            _org, _pat = await resolve_auth(
                tenant_id or "", project_id=project_id or "", owner_id=owner_id or "",
            )
            ado_url = _build_ado_remote_url(_org, project, repo, _pat) if _pat else ""
            if ado_url:
                await loop.run_in_executor(
                    None, _add_ado_remote_and_fetch_main, fast_dest, ado_url,
                )
            else:
                # Not connected → fall back to normal fetch_base_branch (which will
                # also fail on a local-only origin, but at least it tries).
                await loop.run_in_executor(None, fetch_base_branch, fast_dest, "main")
        except Exception as exc:
            logger.warning(f"Phase 8.1: base-branch fetch on local-reuse failed (PR-scoped will skip): {exc}")
        return {
            "work_dir": fast_dest,
            "repo_workspace": fast_dest,
            "reused_dev_workspace": True,
            # Phase 8.1 — was missing; without pr_branch downstream PR-scoped
            # filter + coverage are silently skipped on the reuse path.
            "pr_branch": branch,
        }
    # Reuse failed (path missing / branch not local) — clean up the empty dest
    # and proceed with remote clone.
    shutil.rmtree(fast_dest, ignore_errors=True)

    blog(f"Cloning ADO repo {project}/{repo} branch {branch}...")
    # Resolve ADO creds the same way the Dev/Code-Review/Security agents do: the
    # per-tenant connector secret store (Integrations page), with the legacy env
    # connector as fallback. This is what makes the standalone testing run work
    # when ADO is connected on Integrations but not via env vars.
    org_url = pat = ""
    try:
        from shared.services import ado_repos
        # project_id + owner_id are load-bearing, not optional detail: Azure DevOps
        # credentials live per person per project and the tenant-wide shared fallback
        # was removed, so resolving on tenant alone finds nothing and reports the
        # connector as unconfigured on an org where it is connected. Dev and
        # code-review pass all three; this was the last caller that did not.
        org_url, pat = await ado_repos.resolve_auth(
            tenant_id or "", project_id=project_id or "", owner_id=owner_id or "",
        )
    except Exception as exc:  # noqa: BLE001
        logger.info("ado_clone: connector resolve_auth failed (%s)", exc)
    if not (org_url and pat):
        friendly = (
            f"Cannot clone {project}/{repo}@{branch} from Azure DevOps because "
            f"the **Azure DevOps connector is not configured** for your organization.\n\n"
            f"Open the **Integrations** page and connect Azure DevOps with your PAT, "
            f"then run the test again."
        )
        blog(friendly, level="ERROR")
        return {
            "work_dir": tempfile.mkdtemp(prefix="testing_agent_no_creds_"),
            "clone_error_message": friendly,
        }

    work_dir = tempfile.mkdtemp(prefix="testing_agent_clone_")
    loop = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(
            None,
            clone_branch,
            org_url,
            project or "",
            repo,
            branch,
            pat,
            work_dir,
        )
    except Exception as exc:
        logger.error(f"ado_clone failed: {exc}")
        friendly = (
            f"Failed to clone {project}/{repo}@{branch} from Azure DevOps.\n\n"
            f"Common causes: the branch doesn't exist, the PAT lacks Code (Read) permission, "
            f"or the repo is in a different ADO project than the connector targets.\n\n"
            f"Underlying error: {exc}"
        )
        blog(friendly, level="ERROR")
        return {
            "work_dir": work_dir,
            "clone_error_message": friendly,
        }

    logger.info(f"ADO clone successful — work_dir={work_dir}")
    blog(f"Cloned into workspace: {work_dir}")
    # Phase 7 — fetch the repo's default branch so PR-scoped diff works on
    # this shallow clone. Best-effort: failure here just means PR-scoped
    # features (analysis filter + PR coverage %) are skipped, whole-repo
    # behaviour is unchanged.
    try:
        from agents_orchestrator.testing_agent.tools.pr_diff import detect_default_branch, fetch_base_branch
        base = await loop.run_in_executor(None, detect_default_branch, work_dir)
        if base != branch:  # don't bother if PR branch IS the base
            ok = await loop.run_in_executor(None, fetch_base_branch, work_dir, base)
            if ok:
                logger.info(f"Phase 7: fetched origin/{base} for PR-scoped diff")
    except Exception as exc:
        logger.warning(f"Phase 7: base-branch fetch failed (PR-scoped features will skip): {exc}")
    return {"work_dir": work_dir, "repo_workspace": work_dir, "pr_branch": branch}


def _parse_ado_repo_url(repo_url: str):
    """Extract (project, repo) from an ADO repo URL of the form
    https://dev.azure.com/<org>/<project>/_git/<repo>(.git)?
    Returns None if the URL doesn't match.
    """
    import re
    m = re.match(
        r"https?://[^/]+/[^/]+/([^/]+)/_git/([^/?#]+?)(?:\.git)?/?$",
        repo_url or "",
    )
    if not m:
        return None
    return m.group(1), m.group(2)


async def setup_single_file_workspace(state: SuperAgentState):
    """Phase 2 + Phase M.5: copy a single source file into a tmpdir and let
    the matching language runner generate any auxiliary project files
    (conftest.py for Python, .csproj for .NET, package.json for React)."""
    src = state['input_file_path']
    logger.info(f"Setting up single-file workspace for {src}")
    blog(f"Setting up single-file workspace for {os.path.basename(src)}...")
    work_dir = tempfile.mkdtemp()

    # Dispatch by extension to the right runner — extracted lazily so this
    # node still works in unit tests that mock the runners module.
    ext = os.path.splitext(src)[1].lower()
    ext_to_lang = {
        ".py": "python",
        ".cs": "dotnet",
        ".jsx": "react", ".tsx": "react", ".js": "react", ".ts": "react",
    }
    lang = ext_to_lang.get(ext, "python")
    try:
        from agents_orchestrator.testing_agent.tools.runners import get_runner
        runner = get_runner(lang)
        runner.setup_single_file_workspace(src, work_dir)
        logger.info(f"Single-file workspace ready ({lang}) at: {work_dir}")
        blog(f"Single-file workspace ready ({lang})")
    except Exception as exc:
        # Last-resort fallback: drop the file in the tmpdir as-is so the
        # rest of the pipeline can still scan it.
        logger.warning(f"Runner setup failed ({exc}); falling back to plain copy")
        dst = os.path.join(work_dir, os.path.basename(src))
        shutil.copy(src, dst)

    return {"work_dir": work_dir}
