"""Git + PR tools for the Development Agent.

Handles: clone (ADO / GitHub), feature branch, build/lint runner,
commit, push, draft PR creation, and PR-ready promotion.
Credentials are injected into URLs; never stored in plain files.
"""
from __future__ import annotations

import asyncio
import base64
import os
import pathlib
import re
import subprocess
import urllib.parse
from typing import List, Optional

import httpx
from langchain_core.tools import tool

from agents_orchestrator.development_agent.config.session_state import get_session
from agents_orchestrator.development_agent.tools.sandbox_policy import (
    DEFAULT_POLICY,
    classify_command,
    sanitize_output,
    validate_command,
)
from config.connection_manager import manager
from config.connectors.context import get_connector as get_active_connector
from config.connectors.base import ConnectorNotAvailableError
from config.ws_helper import broadcast_log, get_session_id, get_user_id, get_provider_kind
from shared.models.development import CommandResult, ValidationResult

# git_tools.py → tools/ → development_agent/ → agents_orchestrator/ → agentic_app/
_FILES_DIR = str(pathlib.Path(__file__).resolve().parents[3] / "files")

SAFE_PREFIXES = (
    "npm ", "npx ", "dotnet ", "python ", "pip ", "eslint ",
    "pylint ", "go ", "cargo ", "tsc ", "ng ", "mvn ",
    "yarn ", "pnpm ", "jest ", "pytest ",
)

_ARTIFACT_STDOUT_CAP = 5_000  # 5 KB per field stored in DevelopmentArtifacts


def _record_command(s, command: str, exit_code: int, stdout: str, stderr: str) -> None:
    """Append a CommandResult and optionally a ValidationResult to session dev_artifacts."""
    cmd_result = CommandResult(
        command=command,
        exit_code=exit_code,
        stdout=sanitize_output(stdout, DEFAULT_POLICY)[:_ARTIFACT_STDOUT_CAP],
        stderr=sanitize_output(stderr, DEFAULT_POLICY)[:_ARTIFACT_STDOUT_CAP],
        sanitized=True,
    )
    s.dev_artifacts.commands_run.append(cmd_result)

    kind = classify_command(command)
    if kind in ("test", "build"):
        status = "passed" if exit_code == 0 else "failed"
        vr = ValidationResult(
            name=command[:60],
            status=status,
            command=command,
            summary=f"Exit {exit_code}",
            output=stdout[:2_000],
        )
        if kind == "test":
            s.dev_artifacts.test_results.append(vr)
        else:
            s.dev_artifacts.build_results.append(vr)


# ── Internal helpers ──────────────────────────────────────────────────────────

def _get_work_dir() -> str:
    session_id = get_session_id()
    s = get_session(session_id)
    if s.work_dir:
        return s.work_dir
    user_id = get_user_id()
    work_dir = os.path.join(_FILES_DIR, str(user_id), "orchestrator", str(session_id), "project")
    os.makedirs(work_dir, exist_ok=True)
    return work_dir


# Force git to use ONLY the PAT embedded in the clone URL: disable any system
# credential helper (Git Credential Manager on Windows hijacks dev.azure.com auth with
# a stale cached credential → 'Authentication failed' despite a valid PAT) and never
# fall back to an interactive prompt (which hangs a headless worker).
_GIT_NO_HELPER = ["-c", "credential.helper=", "-c", "credential.useHttpPath=true"]


def _run_git(args: list, cwd: str, timeout: int = 120) -> str:
    result = subprocess.run(
        ["git"] + _GIT_NO_HELPER + args,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never"},
    )
    out = result.stdout.strip()
    err = result.stderr.strip()
    if result.returncode != 0:
        return f"ERROR (exit {result.returncode}): {err}\n{out}"
    # Include stderr on success — git push writes tracking messages there
    combined = "\n".join(filter(None, [out, err]))
    return combined or "OK"


def _inject_credentials(url: str, pat_or_token: str) -> str:
    """Inject PAT/token into an HTTPS clone URL.

    The token is percent-encoded — ADO/GitHub tokens can contain URL-reserved
    characters (/, +, =, :) that otherwise mangle the netloc and surface as
    'fatal: Authentication failed' on clone. A replacement *function* is used so a
    backslash in the token isn't interpreted as a regex backreference.
    """
    safe = urllib.parse.quote(pat_or_token, safe="")
    return re.sub(r"https://(?:[^@]+@)?", lambda _m: f"https://{safe}@", url, count=1)


def _configure_git_identity(cwd: str) -> None:
    subprocess.run(["git", "config", "user.email", "dev-agent@aisdlc.ai"], cwd=cwd, capture_output=True)
    subprocess.run(["git", "config", "user.name", "AI Dev Agent"], cwd=cwd, capture_output=True)


_BRANCH_MAX_LEN = 100


def _sanitize_branch_name(name: str) -> str:
    """Normalise and length-cap a branch name without splitting mid-word.

    ADO and Git both support names well beyond 100 chars, but very long names
    are unwieldy in the UI. If the name exceeds _BRANCH_MAX_LEN we trim at the
    last '-' or '/' separator before the limit so we never cut inside a word.
    """
    name = name.strip()
    if len(name) <= _BRANCH_MAX_LEN:
        return name
    truncated = name[:_BRANCH_MAX_LEN]
    # Walk back to the last separator so we don't cut mid-word
    cut = max(truncated.rfind("-"), truncated.rfind("/"))
    return truncated[:cut] if cut > 0 else truncated


def _set_ado_default_branch(s, project: str, repo_id: str, branch: str) -> None:
    """PATCH the ADO repo to set the default branch so the Files tab shows content."""
    pat, org_url = s.pat, s.ado_org_url
    if not pat or not org_url:
        return
    import urllib.parse
    encoded_project = urllib.parse.quote(project, safe="")
    url = f"{org_url.rstrip('/')}/{encoded_project}/_apis/git/repositories/{repo_id}?api-version=7.0"
    auth = base64.b64encode(f":{pat}".encode()).decode()
    try:
        resp = httpx.patch(
            url,
            json={"defaultBranch": f"refs/heads/{branch}"},
            headers={"Authorization": f"Basic {auth}", "Content-Type": "application/json"},
            timeout=15,
        )
        if resp.is_success:
            broadcast_log(manager, f"Set ADO default branch to '{branch}'", level="INFO")
    except Exception:
        pass


# ── Tools ─────────────────────────────────────────────────────────────────────

async def _active_ado_creds() -> tuple[str, str]:
    """Resolve (org_url, pat) for ADO, preferring the per-run tenant connector the
    orchestrator bound (config.connectors.context) over the global env vars. This is
    what lets a pipeline run use the tenant's Integrations-page ADO credentials
    (secret store). With no connector bound, this returns ("", "") — there is no
    process-wide PAT to fall back on."""
    try:
        conn = get_active_connector()  # AzureDevOpsConnector bound for this run's stage
        auth = await conn.auth_adapter()  # {org_url, pat} — tenant secret store
        org = (auth.get("org_url") or "").rstrip("/")
        pat = auth.get("pat") or ""
        if pat:
            return org, pat
    except Exception:  # noqa: BLE001 — no active connector (standalone)
        pass
    return "", ""


@tool
async def get_ado_context() -> str:
    """Retrieve Azure DevOps org URL from the configured connector and store credentials in session.

    Call this BEFORE list_ado_projects / clone_repo.
    Credentials are stored securely in the session — pass "from_session" as pat_or_token to clone_repo.
    After calling this, ALWAYS call list_ado_projects to show available projects — never ask the user to type a project name.
    """
    import json as _json
    org_url, pat = await _active_ado_creds()

    if not pat:
        return _json.dumps({
            "error": "No ADO credentials configured.",
            "action": "Ask the user to configure the ADO connector in the Integrations page, or provide their org URL and PAT manually.",
        })

    session_id = get_session_id()
    s = get_session(session_id)
    s.pat = pat
    s.ado_org_url = org_url

    return _json.dumps({
        "org_url": org_url,
        "credentials_stored": True,
        "note": "Credentials stored. Call list_ado_projects next to show the project list to the user. STOP after showing the list — wait for the user to pick a project before calling list_ado_repos.",
    })


@tool
async def list_ado_projects() -> str:
    """List all ADO projects available in the configured organisation.

    Call this immediately after get_ado_context — never ask the user to type a project name.
    Present the list to the user and ask which project to work on.
    """
    from shared.services import ado_repos

    org_url, pat = await _active_ado_creds()
    if not pat:
        return "Error: No ADO credentials configured. Ask the user to set up the ADO connector."

    try:
        projects = await ado_repos.list_projects(pat=pat, org_url=org_url)
    except Exception as exc:
        return f"Error fetching ADO projects: {exc}"

    if not projects:
        return "No projects found in this ADO organisation."

    lines = [f"{i+1}. {p['name']}" for i, p in enumerate(projects)]
    return (
        "ADO projects:\n" + "\n".join(lines) +
        "\n\nWhich project do you want to work in? (reply with the number or name)"
        "\nDo NOT call list_ado_repos until the user replies."
    )


@tool
async def list_ado_repos(project: str) -> str:
    """List all Git repositories in an ADO project.

    Call this after the user picks a project from list_ado_projects.
    Present the repo list and ask which one to clone — never ask the user to type a repo name.

    Args:
        project: Exact ADO project name as returned by list_ado_projects.
    """
    from shared.services import ado_repos

    org_url, pat = await _active_ado_creds()
    if not pat:
        return "Error: No ADO credentials configured."

    try:
        repos = await ado_repos.list_repos(project, pat=pat, org_url=org_url)
    except Exception as exc:
        return f"Error fetching repos for project '{project}': {exc}"

    if not repos:
        return f"No Git repositories found in project '{project}'."

    # Store full URLs in session for later cloning without exposing credentials in text
    session_id = get_session_id()
    s = get_session(session_id)
    s.ado_project = project
    s.ado_repos = {r["name"]: r.get("remote_url", "") for r in repos}
    # Store repo IDs so list_ado_branches can use them without re-fetching
    s._ado_repo_ids = {r["name"]: r.get("id", "") for r in repos}

    lines = [f"{i+1}. {r['name']}" for i, r in enumerate(repos)]
    return (
        f"Repositories in '{project}':\n" + "\n".join(lines) +
        "\n\nWhich repository should I use? Reply with the number, or give a new name to create a fresh one."
        "\nDo NOT call clone_repo or create_ado_repo until the user replies."
    )


@tool
async def list_ado_branches(repo_name: str = "") -> str:
    """List all branches in the current ADO repository.

    Call this AFTER the repo is confirmed (after clone_repo or after the user picks a repo)
    and BEFORE create_feature_branch. Show the list to the user and ask:
    - Which existing branch to check out, OR
    - What name to use for a new feature branch.

    Args:
        repo_name: Repo name from list_ado_repos. Leave empty if repo is already cloned
            (the session work_dir is used instead).
    """
    session_id = get_session_id()
    s = get_session(session_id)

    # ── Path A: repo already cloned — ask git directly ───────────────────────
    if s.work_dir and os.path.isdir(os.path.join(s.work_dir, ".git")):
        result = subprocess.run(
            ["git", "branch", "-r"],
            cwd=s.work_dir, capture_output=True, text=True,
        )
        remote_branches = [
            b.strip().removeprefix("origin/")
            for b in result.stdout.splitlines()
            if b.strip() and "HEAD" not in b
        ]
        if remote_branches:
            lines = [f"{i+1}. {b}" for i, b in enumerate(remote_branches)]
            return (
                "Remote branches:\n" + "\n".join(lines) +
                "\n\nWhich branch should I base the feature branch on? "
                "Or type a new branch name to create one (e.g. 'feature/my-feature')."
                "\nDo NOT call create_feature_branch until the user replies."
            )
        return (
            "No remote branches found (repo may be empty). "
            "What should I name the feature branch? (e.g. 'feature/my-feature')"
            "\nDo NOT call create_feature_branch until the user replies."
        )

    # ── Path B: repo not yet cloned — call ADO API via shared helper ─────────
    from shared.services import ado_repos as _ado_repos

    pat = s.pat
    project = s.ado_project
    if not pat or not s.ado_org_url or not project:
        return (
            "Cannot list branches yet — repo not cloned and ADO context not set. "
            "Call get_ado_context and list_ado_repos first."
        )

    # Resolve repo_id from session cache; avoids an extra API round-trip
    repo_ids: dict = getattr(s, "_ado_repo_ids", {}) or {}
    if not repo_name and s.ado_repo_name:
        repo_name = s.ado_repo_name
    repo_id = repo_ids.get(repo_name) or s.ado_repo_id

    if not repo_id:
        return (
            f"Cannot find repo ID for '{repo_name}'. "
            "Call list_ado_repos first to populate the repo list."
        )

    try:
        branch_items = await _ado_repos.list_branches(project, repo_id, pat=pat, org_url=s.ado_org_url)
    except Exception as exc:
        return f"Error listing branches: {exc}"

    branches = [b["name"] for b in branch_items]
    if not branches:
        return (
            f"No branches found in '{repo_name}' (it may be an empty repo). "
            "What should I name the feature branch?"
            "\nDo NOT call create_feature_branch until the user replies."
        )

    lines = [f"{i+1}. {b}" for i, b in enumerate(branches)]
    return (
        f"Branches in '{repo_name}':\n" + "\n".join(lines) +
        "\n\nWhich branch should I base the feature branch on? "
        "Or type a new branch name to create one (e.g. 'feature/my-feature')."
        "\nDo NOT call create_feature_branch until the user replies."
    )


@tool
async def create_ado_repo(project: str, repo_name: str) -> str:
    """Create a new Git repository in ADO — use for greenfield projects before clone_repo.

    After creation, call clone_repo(clone_url, 'from_session') with the returned URL.

    Args:
        project: ADO project name (from list_ado_projects)
        repo_name: Name for the new repository (kebab-case recommended)
    """
    import urllib.parse

    session_id = get_session_id()
    s = get_session(session_id)
    pat = s.pat
    org_url = (s.ado_org_url or "").rstrip("/")

    if not org_url or not pat:
        return "ADO credentials not available. Call get_ado_context() first."

    token = base64.b64encode(f":{pat}".encode()).decode()
    encoded_project = urllib.parse.quote(project, safe="")
    url = f"{org_url}/{encoded_project}/_apis/git/repositories?api-version=7.0"

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                url,
                headers={"Authorization": f"Basic {token}", "Content-Type": "application/json"},
                json={"name": repo_name},
            )
            if resp.status_code == 409:
                return (
                    f"Repository '{repo_name}' already exists in project '{project}'. "
                    "Call list_ado_repos to find its clone URL, then clone it."
                )
            if not resp.is_success:
                return f"ADO API error {resp.status_code} creating repo '{repo_name}': {resp.text}"
            data = resp.json()
    except Exception as e:
        return f"Error creating repository: {e}"

    clone_url = data.get("remoteUrl", "")
    repo_id = data.get("id", "")
    s.repo_url = clone_url
    s.repo_type = "ado"
    s.ado_project = project
    s.ado_repo_id = repo_id
    s.ado_repo_name = repo_name
    if not hasattr(s, "ado_repos") or s.ado_repos is None:
        s.ado_repos = {}
    s.ado_repos[repo_name] = clone_url

    return (
        f"Repository '{repo_name}' created in project '{project}'.\n"
        f"Now call: clone_repo('{repo_name}', 'from_session') to clone it."
    )


@tool
async def get_github_context() -> str:
    """Bootstrap a GitHub session — load credentials from the GitHub connector and confirm access.

    Call this BEFORE list_github_repos / clone_repo for GitHub repositories.
    Credentials are stored in the session. Call list_github_repos next.
    """
    import json as _json

    session_id = get_session_id()
    s = get_session(session_id)

    # GitHub credentials are per-tenant: they come from the connector bound to this
    # run (tenant secret store / that tenant's Key Vault). There is no process-wide
    # GitHub PAT, so an unconnected tenant gets a clean instruction instead.
    gh_org_url, gh_token = "", ""
    try:
        auth = await get_active_connector().auth_adapter()
        gh_token = auth.get("pat") or auth.get("token") or ""
        gh_org_url = auth.get("org_url", "")
    except Exception:  # noqa: BLE001 — no connector bound to this run
        gh_token = ""

    if not gh_token:
        return _json.dumps({
            "error": "GitHub is not connected for your organization.",
            "action": "Open Integrations in the sidebar and connect GitHub (PAT with repo, workflow scopes).",
        })

    s.pat = gh_token
    s.repo_type = "github"

    try:
        resp = httpx.get(
            "https://api.github.com/user",
            headers={"Authorization": f"Bearer {s.pat}", "Accept": "application/vnd.github+json"},
            timeout=10,
        )
        if resp.is_success:
            login = resp.json().get("login", "unknown")
            return _json.dumps({
                "authenticated_as": login,
                "org_url": gh_org_url,
                "credentials_stored": True,
                "note": "GitHub credentials stored. Call list_github_repos next to show available repos.",
            })
        return _json.dumps({"error": f"GitHub auth failed ({resp.status_code}). Check PAT scopes: repo, workflow."})
    except Exception as e:
        return _json.dumps({"error": f"GitHub connection error: {e}"})


@tool
async def list_github_repos(org_or_user: str) -> str:
    """List GitHub repositories for an organisation or user.

    Call this after get_github_context. Present the list and ask which repo to use.

    Args:
        org_or_user: GitHub organisation name or username (not the full URL).
    """
    session_id = get_session_id()
    s = get_session(session_id)
    pat = s.pat
    if not pat:
        return "GitHub credentials not loaded. Call get_github_context() first."

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            headers = {"Authorization": f"Bearer {pat}", "Accept": "application/vnd.github+json"}
            resp = await client.get(
                f"https://api.github.com/orgs/{org_or_user}/repos?per_page=50&sort=updated",
                headers=headers,
            )
            if resp.status_code == 404:
                resp = await client.get(
                    f"https://api.github.com/users/{org_or_user}/repos?per_page=50&sort=updated",
                    headers=headers,
                )
            resp.raise_for_status()
            repos = resp.json()
    except Exception as e:
        return f"Error listing GitHub repos: {e}"

    if not repos:
        return f"No repositories found for '{org_or_user}'."

    lines = [f"{i+1}. {r['name']}  (default branch: {r.get('default_branch', 'main')})" for i, r in enumerate(repos)]
    return (
        f"GitHub repositories for '{org_or_user}':\n" + "\n".join(lines) +
        "\n\nWhich repository should I use? Reply with the number or name."
        "\nDo NOT call clone_repo until the user replies."
    )


@tool
async def list_github_branches(owner: str, repo: str) -> str:
    """List branches in a GitHub repository.

    Call this after the repo is confirmed and before create_feature_branch.

    Args:
        owner: GitHub organisation or user that owns the repo.
        repo: Repository name.
    """
    session_id = get_session_id()
    s = get_session(session_id)

    if s.work_dir and os.path.isdir(os.path.join(s.work_dir, ".git")):
        result = subprocess.run(["git", "branch", "-r"], cwd=s.work_dir, capture_output=True, text=True)
        branches = [
            b.strip().removeprefix("origin/")
            for b in result.stdout.splitlines()
            if b.strip() and "HEAD" not in b
        ]
        if branches:
            lines = [f"{i+1}. {b}" for i, b in enumerate(branches)]
            return (
                "Remote branches:\n" + "\n".join(lines) +
                "\n\nWhich branch should I base the feature branch on? "
                "Or type a new name (e.g. 'feature/PROJ-42-add-login')."
                "\nDo NOT call create_feature_branch until the user replies."
            )

    pat = s.pat
    if not pat:
        return "GitHub credentials not loaded. Call get_github_context() first."

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(
                f"https://api.github.com/repos/{owner}/{repo}/branches?per_page=50",
                headers={"Authorization": f"Bearer {pat}", "Accept": "application/vnd.github+json"},
            )
            resp.raise_for_status()
            branches = [b["name"] for b in resp.json()]
    except Exception as e:
        return f"Error listing GitHub branches: {e}"

    if not branches:
        return f"No branches found in '{owner}/{repo}' — it may be an empty repo."

    lines = [f"{i+1}. {b}" for i, b in enumerate(branches)]
    return (
        f"Branches in '{owner}/{repo}':\n" + "\n".join(lines) +
        "\n\nWhich branch should I base the feature branch on? "
        "Or type a new name (e.g. 'feature/PROJ-42-add-login')."
        "\nDo NOT call create_feature_branch until the user replies."
    )


@tool
async def create_github_pr_tool(
    owner: str,
    repo: str,
    head: str,
    base: str,
    title: str,
    body: str = "",
) -> str:
    """Create a GitHub draft pull request.

    Args:
        owner: GitHub organisation or user that owns the repo.
        repo: Repository name.
        head: Source branch (the feature branch to merge from).
        base: Target branch (usually 'main').
        title: Pull request title.
        body: PR description in markdown.
    """
    session_id = get_session_id()
    s = get_session(session_id)

    if not s.pat:
        return "GitHub credentials not loaded. Call get_github_context() first."

    if not s.repo_url or "github.com" not in s.repo_url:
        s.repo_url = f"https://github.com/{owner}/{repo}"
        s.repo_type = "github"
    if not s.branch_name:
        s.branch_name = head

    if get_provider_kind() == "jira" and not s.jira_base_url:
        try:
            _conn = get_active_connector()
            s.jira_base_url = _conn._org_url
        except Exception:
            pass

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _create_github_pr, s, title, body, base)


@tool
def clone_repo(repo_url: str, pat_or_token: Optional[str] = None) -> str:
    """Clone a repository into the session working directory.
    Injects the PAT/token into the HTTPS URL so no interactive auth is needed.
    Supports both Azure DevOps (ADO) and GitHub URLs.

    Args:
        repo_url: Repository name (from list_ado_repos) OR full HTTPS URL.
            Preferred: pass the repo name — URL is resolved from the list_ado_repos cache.
            ADO full URL example:    https://dev.azure.com/org/project/_git/repo
            GitHub full URL example: https://github.com/owner/repo
        pat_or_token: Personal access token. Pass "from_session" or omit to use
            credentials already stored by get_ado_context.
    """
    session_id = get_session_id()
    user_id = get_user_id()
    s = get_session(session_id)
    if not pat_or_token or pat_or_token == "from_session":
        pat_or_token = s.pat  # prefer credential already stored by get_ado_context / get_github_context

    # Resolve repo name → full URL if the agent passed a name instead of a URL
    if repo_url and not repo_url.startswith("http"):
        stored = getattr(s, "ado_repos", None) or {}
        resolved = stored.get(repo_url)
        if resolved:
            s.ado_repo_name = repo_url  # keep the human-readable name
            if not s.ado_repo_id:
                repo_ids: dict = getattr(s, "_ado_repo_ids", {}) or {}
                s.ado_repo_id = repo_ids.get(repo_url, "")
            repo_url = resolved
        else:
            return (
                f"Cannot resolve repo name '{repo_url}' to a URL. "
                "Call list_ado_repos first, then clone_repo with the repo name."
            )

    project_base = os.path.join(_FILES_DIR, str(user_id), "orchestrator", str(session_id), "project")
    os.makedirs(project_base, exist_ok=True)

    s.repo_type = "ado" if ("dev.azure.com" in repo_url or "visualstudio.com" in repo_url) else "github"
    s.repo_url = repo_url

    # Resolve PAT by repo type if not already set
    if not pat_or_token:
        # The session already holds THIS TENANT's credential: get_ado_context /
        # get_github_context resolved it from the run's connector and stored it.
        # There is no process-wide PAT to fall back on if they were never called.
        pat_or_token = s.pat

    s.pat = pat_or_token
    s.build_attempts = 0

    auth_url = _inject_credentials(repo_url, pat_or_token)
    broadcast_log(manager, f"Cloning: {repo_url}", level="INFO")

    result = subprocess.run(
        ["git", *_GIT_NO_HELPER, "clone", auth_url, "."],
        cwd=project_base,
        capture_output=True,
        text=True,
        timeout=300,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never"},
    )
    if result.returncode != 0:
        err = result.stderr.strip().replace(pat_or_token, "***")
        return f"Clone failed: {err}"

    _configure_git_identity(project_base)
    s.work_dir = project_base
    s.dev_artifacts.repo_url = repo_url
    s.dev_artifacts.repo_type = s.repo_type
    s.dev_artifacts.status = "in_progress"

    # If the repo is empty (no commits), seed a main branch so feature branches
    # have a real base. Without this, push_branch later creates main with the same
    # commits as the feature branch, giving ADO "no diff" and a 400 on PR creation.
    no_commits = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=project_base, capture_output=True, text=True,
    ).returncode != 0
    if no_commits:
        # Write a minimal .gitignore so the initial commit has real content
        gitignore = os.path.join(project_base, ".gitignore")
        if not os.path.exists(gitignore):
            with open(gitignore, "w") as f:
                f.write("node_modules/\n__pycache__/\n*.pyc\n.env\ndist/\nbuild/\n")
        # After cloning an empty ADO repo, HEAD already points to 'main' (unborn).
        # Don't checkout -b main — just commit and push directly.
        # Discover the current unborn branch name to use in the push command.
        sym = subprocess.run(
            ["git", "symbolic-ref", "--short", "HEAD"],
            cwd=project_base, capture_output=True, text=True,
        )
        default_branch = sym.stdout.strip() or "main"
        subprocess.run(["git", "add", ".gitignore"], cwd=project_base, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "chore: initial commit"],
            cwd=project_base, capture_output=True,
        )
        push_main = subprocess.run(
            ["git", *_GIT_NO_HELPER, "push", "--set-upstream", "origin", default_branch],
            cwd=project_base, capture_output=True, text=True, timeout=60,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never"},
        )
        if push_main.returncode == 0:
            broadcast_log(manager, f"Seeded '{default_branch}' branch on remote (PR target)", level="INFO")
            # Set default branch in ADO so the Files tab shows content
            repo_id = getattr(s, "ado_repo_id", "")
            ado_project = getattr(s, "ado_project", "")
            if repo_id and ado_project:
                _set_ado_default_branch(s, ado_project, repo_id, default_branch)
        else:
            broadcast_log(manager, f"Warning: could not seed {default_branch}: {push_main.stderr[:300]}", level="WARN")

    broadcast_log(manager, f"Cloned to: {project_base}", level="INFO")
    return f"Successfully cloned to: {project_base}"


@tool
def create_feature_branch(branch_name: str) -> str:
    """Create and checkout a new feature branch in the working directory.

    Args:
        branch_name: kebab-case branch name e.g. feature/89-expense-submission
    """
    session_id = get_session_id()
    s = get_session(session_id)
    work_dir = _get_work_dir()
    branch_name = _sanitize_branch_name(branch_name)
    # HEAD before branching is the diff base for the changed-files/lines decorations.
    base_head = _run_git(["rev-parse", "HEAD"], cwd=work_dir)
    broadcast_log(manager, f"Creating branch: {branch_name}", level="INFO")
    result = _run_git(["checkout", "-b", branch_name], cwd=work_dir)
    if "ERROR" not in result:
        s.branch_name = branch_name
        s.dev_artifacts.branch_name = branch_name
        if base_head and "ERROR" not in base_head:
            try:
                s.base_sha = base_head.strip()
            except Exception:  # noqa: BLE001
                pass
    return result


@tool
def run_command(command: str) -> str:
    """Run a build, lint, or test command in the working directory.
    Returns combined stdout + stderr. Timeout: 120 s.

    Only the following command prefixes are allowed:
    npm, npx, dotnet, python, pip, eslint, pylint, go, cargo,
    tsc, ng, mvn, yarn, pnpm, jest, pytest.

    Args:
        command: Shell command to execute (must start with an allowed prefix).
    """
    if not any(command.strip().startswith(p) for p in SAFE_PREFIXES):
        allowed = ", ".join(p.strip() for p in SAFE_PREFIXES)
        return f"Error: command not allowed. Must start with one of: {allowed}"

    operator_error = validate_command(command)
    if operator_error:
        return f"Error: {operator_error}"

    session_id = get_session_id()
    s = get_session(session_id)
    work_dir = _get_work_dir()
    s.build_attempts += 1
    broadcast_log(manager, f"Running (attempt {s.build_attempts}): {command}", level="INFO")

    try:
        result = subprocess.run(
            command,
            cwd=work_dir,
            capture_output=True,
            text=True,
            timeout=120,
            shell=True,
        )
        _record_command(s, command, result.returncode, result.stdout, result.stderr)
        raw = (
            f"Exit code: {result.returncode}\n"
            f"STDOUT:\n{result.stdout}\n"
            f"STDERR:\n{result.stderr}"
        )
        return sanitize_output(raw)
    except subprocess.TimeoutExpired:
        _record_command(s, command, -1, "", "timed out")
        return "Error: command timed out after 120 seconds."
    except Exception as e:
        return f"Error running command: {e}"


@tool
def git_commit(message: str) -> str:
    """Stage all changes and create a git commit in the working directory.

    Args:
        message: Commit message.
    """
    work_dir = _get_work_dir()
    broadcast_log(manager, f"Committing: {message}", level="INFO")
    stage = _run_git(["add", "-A"], cwd=work_dir)
    if "ERROR" in stage:
        return f"Stage failed: {stage}"
    return _run_git(["commit", "-m", message], cwd=work_dir)


def _pending_push_diff(work_dir: str) -> str:
    """Best-effort diff of what a push would send — unpushed commits vs upstream,
    else vs main, else the last commit. Capped so it stays chat-friendly."""
    stat = ""
    for rng in ("@{u}..HEAD", "origin/main..HEAD", "main..HEAD"):
        out = _run_git(["diff", "--stat", rng], cwd=work_dir)
        if "ERROR" not in out and out.strip():
            stat = out
            break
    if not stat:
        stat = _run_git(["show", "--stat", "--oneline", "HEAD"], cwd=work_dir)
    body = ""
    for rng in ("@{u}..HEAD", "origin/main..HEAD", "main..HEAD"):
        out = _run_git(["diff", rng], cwd=work_dir)
        if "ERROR" not in out and out.strip():
            body = out
            break
    if not body:
        body = _run_git(["show", "HEAD"], cwd=work_dir)
    if len(body) > 6000:
        body = body[:6000] + "\n… (diff truncated)"
    return f"{stat.strip()}\n\n```diff\n{body.strip()}\n```"


@tool
def push_branch() -> str:
    """Push the current feature branch to origin.
    Credentials are already embedded in the remote URL from clone_repo.
    Also creates 'main' on the remote if it doesn't exist, so PR creation works.
    """
    session_id = get_session_id()
    s = get_session(session_id)
    work_dir = _get_work_dir()
    branch = s.branch_name or "HEAD"

    # HITL gate (standalone dev agent only — orchestrator/pipeline leave
    # push_gate_enabled False). Never push until the user has approved THIS turn;
    # on the first pass return the diff + an explicit "ask and stop" instruction
    # instead of pushing, so the user always sees the change and confirms first.
    if getattr(s, "push_gate_enabled", False) and not getattr(s, "push_approved", False):
        broadcast_log(manager, "Showing diff — awaiting push approval", level="INFO")
        diff = _pending_push_diff(work_dir)
        return (
            "⛔ NOT PUSHED — this is NOT an error. The change is committed locally on "
            f"branch '{branch}' but must be approved before pushing.\n"
            "Do this now, then STOP: (1) show the user the diff below, (2) ask "
            f"\"Shall I push '{branch}' to the remote and open a PR?\". Do NOT call "
            "push_branch or create_pr again until the user replies with approval "
            "(e.g. \"push\" / \"yes\").\n\n"
            f"Diff to be pushed:\n{diff}"
        )

    broadcast_log(manager, f"Pushing: {branch}", level="INFO")
    result = _run_git(["push", "--set-upstream", "origin", branch], cwd=work_dir)
    return result


def _existing_pr_or_none(s) -> Optional[str]:
    """Return the already-recorded PR URL for this session, or None.

    Checked at the start of create_pr so that a retry within the same
    session does not open a second pull request for the same branch.
    """
    url = getattr(s.dev_artifacts, "pr_url", None) or getattr(s, "pr_url", None)
    return url if url else None


@tool
async def create_pr(
    title: str,
    description: str,
    work_item_ids: Optional[List[str]] = None,
    target_branch: str = "main",
) -> str:
    """Create a draft pull request for Azure Repos or GitHub.

    Azure Repos links numeric Azure Boards work items when work_item_ids are
    provided. Jira sessions should include the Jira key in branch/title/body;
    the Jira browse URL is injected into the PR description separately.

    Args:
        title: Pull request title.
        description: PR body in markdown. Must include sections:
            ## Summary, ## Files Changed, ## How to Test, ## Work Items.
        work_item_ids: Azure Boards IDs for Azure Repos PR linking.
        target_branch: Target/base branch (default 'main').
    """
    if work_item_ids is None:
        work_item_ids = []

    session_id = get_session_id()
    s = get_session(session_id)

    # HITL gate (standalone dev agent only). Backstop for push_branch's gate —
    # never open a PR unprompted when the gate is enabled and unapproved.
    if getattr(s, "push_gate_enabled", False) and not getattr(s, "push_approved", False):
        return (
            "⛔ NOT CREATED — awaiting the user's approval (not an error). Show the "
            "user the diff and ask \"Shall I push and open a PR?\", then STOP. Only "
            "call create_pr after the user replies with approval."
        )

    if not s.repo_url:
        return "Error: no repo URL in session. Call clone_repo first."
    if not s.branch_name:
        return "Error: no branch name in session. Call create_feature_branch first."

    existing = _existing_pr_or_none(s)
    if existing:
        return f"PR already exists for this run: {existing}"

    if get_provider_kind() == "jira" and not s.jira_base_url:
        try:
            _conn = get_active_connector()
            s.jira_base_url = _conn._org_url
        except Exception:
            pass

    broadcast_log(manager, f"Creating draft PR: {title}", level="INFO")
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None,
        _create_pr_sync,
        s, title, description, work_item_ids, target_branch,
    )


def _inject_jira_link(s, description: str) -> str:
    """Append a Jira browse link to a PR description when the session is Jira-backed."""
    if not s.jira_base_url or not s.branch_name:
        return description
    m = re.search(r"[A-Z][A-Z0-9]+-\d+", s.branch_name)
    if not m:
        return description
    key = m.group(0)
    jira_url = f"{s.jira_base_url.rstrip('/')}/browse/{key}"
    if jira_url in description:
        return description
    return description + f"\n\nJira: {jira_url}"


def _create_pr_sync(s, title, description, work_item_ids, target_branch):
    try:
        if s.repo_type == "ado":
            return _create_ado_pr(s, title, description, work_item_ids, target_branch)
        else:
            return _create_github_pr(s, title, description, target_branch)
    except Exception as e:
        return f"PR creation failed: {e}"


def _create_ado_pr(s, title, description, work_item_ids, target_branch):
    match = re.match(
        r"https://(?:[^@]+@)?dev\.azure\.com/([^/]+)/([^/]+)/_git/([^/?#]+)",
        s.repo_url,
    )
    if not match:
        return f"Cannot parse ADO URL: {s.repo_url}"
    org, project, repo = match.group(1), match.group(2), match.group(3)

    api_url = (
        f"https://dev.azure.com/{org}/{project}/_apis/git/repositories/{repo}"
        f"/pullrequests?api-version=7.1"
    )
    pat = s.pat
    auth = base64.b64encode(f":{pat}".encode()).decode()

    body: dict = {
        "title": title,
        "description": description,
        "sourceRefName": f"refs/heads/{s.branch_name}",
        "targetRefName": f"refs/heads/{target_branch}",
        "isDraft": True,
    }
    if work_item_ids:
        body["workItemRefs"] = [{"id": str(wid)} for wid in work_item_ids]

    body["description"] = _inject_jira_link(s, description)

    resp = httpx.post(
        api_url,
        json=body,
        headers={"Authorization": f"Basic {auth}", "Content-Type": "application/json"},
        timeout=30,
    )
    if not resp.is_success:
        return f"ADO PR creation failed ({resp.status_code}): {resp.text}"
    data = resp.json()
    pr_id = data.get("pullRequestId")
    pr_url = f"https://dev.azure.com/{org}/{project}/_git/{repo}/pullrequest/{pr_id}"
    s.pr_url = pr_url
    s.pr_title = title
    s.dev_artifacts.pr_url = pr_url
    s.dev_artifacts.pr_title = title
    s.dev_artifacts.status = "pr_created"
    if work_item_ids:
        s.dev_artifacts.work_item_ids = [str(w) for w in work_item_ids]
    return f"Draft PR created: {pr_url}"


def _create_github_pr(s, title, description, target_branch):
    clean = s.repo_url.rstrip("/").removesuffix(".git")
    match = re.match(r"https://(?:[^@]+@)?github\.com/([^/]+)/([^/?#]+)", clean)
    if not match:
        return f"Cannot parse GitHub URL: {s.repo_url}"
    owner, repo = match.group(1), match.group(2)

    resp = httpx.post(
        f"https://api.github.com/repos/{owner}/{repo}/pulls",
        json={
            "title": title,
            "body": _inject_jira_link(s, description),
            "head": s.branch_name,
            "base": target_branch,
            "draft": True,
        },
        headers={"Authorization": f"Bearer {s.pat}", "Accept": "application/vnd.github+json"},
        timeout=30,
    )
    resp.raise_for_status()
    pr_url = resp.json().get("html_url", "")
    s.pr_url = pr_url
    s.pr_title = title
    s.dev_artifacts.pr_url = pr_url
    s.dev_artifacts.pr_title = title
    s.dev_artifacts.status = "pr_created"
    return f"Draft PR created: {pr_url}"


@tool
async def mark_pr_ready(pr_url: str) -> str:
    """Mark a draft pull request as ready for review (removes draft status).
    Auto-detects ADO vs GitHub from the PR URL.

    Args:
        pr_url: The full PR URL returned by create_pr.
    """
    session_id = get_session_id()
    s = get_session(session_id)
    pat = s.pat
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _mark_pr_ready_sync, pr_url, s.repo_type, pat)


def _mark_pr_ready_sync(pr_url, repo_type, pat):
    try:
        if repo_type == "ado" or "dev.azure.com" in pr_url:
            return _mark_ado_pr_ready(pr_url, pat)
        return _mark_github_pr_ready(pr_url, pat)
    except Exception as e:
        return f"Failed to mark PR ready: {e}"


def _mark_ado_pr_ready(pr_url, pat):
    match = re.match(
        r"https://dev\.azure\.com/([^/]+)/([^/]+)/_git/([^/]+)/pullrequest/(\d+)",
        pr_url,
    )
    if not match:
        return f"Cannot parse ADO PR URL: {pr_url}"
    org, project, repo, pr_id = match.group(1), match.group(2), match.group(3), match.group(4)
    auth = base64.b64encode(f":{pat}".encode()).decode()
    resp = httpx.patch(
        f"https://dev.azure.com/{org}/{project}/_apis/git/repositories/{repo}/pullrequests/{pr_id}?api-version=7.1",
        json={"isDraft": False},
        headers={"Authorization": f"Basic {auth}", "Content-Type": "application/json"},
        timeout=30,
    )
    resp.raise_for_status()
    return f"PR is now ready for review: {pr_url}"


def _mark_github_pr_ready(pr_url, token):
    match = re.match(r"https://github\.com/([^/]+)/([^/]+)/pull/(\d+)", pr_url)
    if not match:
        return f"Cannot parse GitHub PR URL: {pr_url}"
    owner, repo, number = match.group(1), match.group(2), match.group(3)
    resp = httpx.patch(
        f"https://api.github.com/repos/{owner}/{repo}/pulls/{number}",
        json={"draft": False},
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
        timeout=30,
    )
    resp.raise_for_status()
    return f"PR is now ready for review: {pr_url}"


@tool
def init_project_structure(tech_stack: str, project_name: str) -> str:
    """Initialize a new project folder structure from scratch (greenfield mode).
    Call this when no repo URL is provided and the project needs to be created fresh.

    Tech stack → scaffold:
    - "react":     public/, src/components/, src/pages/, src/api/, src/App.js, package.json
    - "django":    {project}/__init__.py, settings.py, urls.py, wsgi.py; manage.py; requirements.txt
    - "fastapi":   routers/, models/, schemas/, main.py, requirements.txt
    - "fullstack": frontend/src/, backend/apps/, docker-compose.yml
    - "python":    src/__init__.py, tests/__init__.py, requirements.txt, pyproject.toml

    Args:
        tech_stack: One of "react", "django", "fastapi", "fullstack", "python".
        project_name: Name of the project (used as the folder name).
    """
    session_id = get_session_id()
    user_id = get_user_id()
    s = get_session(session_id)

    base = os.path.join(_FILES_DIR, str(user_id), "orchestrator", str(session_id), "project")
    project_dir = os.path.join(base, project_name)
    os.makedirs(project_dir, exist_ok=True)

    TEMPLATES: dict[str, list[str]] = {
        "react": [
            "public/index.html",
            "src/components/.gitkeep",
            "src/pages/.gitkeep",
            "src/api/.gitkeep",
            "src/App.js",
            "src/index.js",
            "package.json",
        ],
        "django": [
            f"{project_name}/__init__.py",
            f"{project_name}/settings.py",
            f"{project_name}/urls.py",
            f"{project_name}/wsgi.py",
            "apps/.gitkeep",
            "requirements.txt",
            "manage.py",
        ],
        "fastapi": [
            "routers/.gitkeep",
            "models/.gitkeep",
            "schemas/.gitkeep",
            "main.py",
            "requirements.txt",
        ],
        "fullstack": [
            "frontend/src/.gitkeep",
            "frontend/public/.gitkeep",
            "backend/apps/.gitkeep",
            "docker-compose.yml",
        ],
        "python": [
            "src/__init__.py",
            "tests/__init__.py",
            "requirements.txt",
            "pyproject.toml",
        ],
    }

    paths = TEMPLATES.get(tech_stack.lower().strip(), TEMPLATES["python"])
    for rel_path in paths:
        full = os.path.join(project_dir, rel_path)
        os.makedirs(os.path.dirname(os.path.abspath(full)), exist_ok=True)
        if not os.path.exists(full):
            open(full, "w").close()

    subprocess.run(["git", "init"], cwd=project_dir, capture_output=True)
    _configure_git_identity(project_dir)

    s.work_dir = project_dir
    s.repo_type = "local"
    s.build_attempts = 0
    s.dev_artifacts.repo_type = "local"
    s.dev_artifacts.status = "in_progress"

    broadcast_log(manager, f"Initialized {tech_stack} project at {project_dir}", level="INFO")
    return f"Project '{project_name}' initialized at: {project_dir}\nFiles: {', '.join(paths)}"


@tool
async def update_work_item_state(
    project: str,
    work_item_ids: List[int],
    target_state: str,
) -> str:
    """Move PM work items to a new state through the active provider.

    Supports Azure Boards and Jira. For Jira use standard lifecycle intents:
    'To Do', 'In Progress', 'In Review', or 'Done'. Provider fallback maps
    'In Development' to Jira's 'In Progress' when needed.
    """
    if not work_item_ids:
        return "No work item IDs provided - skipping."
    try:
        connector = get_active_connector()
    except RuntimeError as e:
        return f"Connector not available: {e}"

    results = []
    for wid in work_item_ids:
        try:
            result = await connector.write_adapter("move_item_state", project=project, item_id=wid, new_state=target_state)
            moved_to = result.get("new_state") if isinstance(result, dict) else target_state
            moved_to = moved_to or target_state
            results.append(f"#{wid} -> {moved_to}")
            broadcast_log(manager, f"Work item #{wid} moved to '{moved_to}'", level="INFO")
        except Exception as e:
            results.append(f"#{wid} failed: {e}")
    return "\n".join(results)


@tool
async def add_pr_comment_to_work_items(
    project: str,
    work_item_ids: List[int],
    pr_url: str,
) -> str:
    """Post a comment with the PR URL on each linked PM work item."""
    if not work_item_ids:
        return "No work item IDs provided - skipping."
    try:
        connector = get_active_connector()
    except RuntimeError as e:
        return f"Connector not available: {e}"

    comment = f"Pull request created: {pr_url}"
    results = []
    for wid in work_item_ids:
        try:
            await connector.write_adapter("add_comment", project=project, item_id=wid, comment=comment)
            results.append(f"#{wid}: comment added")
        except Exception as e:
            results.append(f"#{wid} failed: {e}")
    return "\n".join(results)
