"""Reaching the legacy repository: list it, and clone it READ-ONLY.

READ-ONLY BY CONSTRUCTION, NOT BY PROMISE. Discovery reads the system being migrated;
nothing it does may change it. So there is no commit, push or branch tool here at all,
the clone is shallow, and the clone's push URL is overwritten with a value git cannot
reach — a stray `git push` from anywhere on this checkout fails instead of writing to
the legacy remote. The Development agent's `clone_repo` is deliberately NOT reused:
it seeds and pushes a `main` branch into an empty repository, which is a write.

CREDENTIALS COME FROM THE STAGE'S CONNECTOR ONLY. The project wires a connector to
the Dependency and Risk stage (project settings → tools); the Orchestrator's
dispatch and the standalone socket both bind it for the turn, and this module reads
it back. A connector the stage may not READ is refused here — `auth_adapter` itself
does not check the level, so this is where that check lives for a git clone.

A public repository needs no credential, but only hosts on `_ALLOWED_HOSTS` are
accepted over plain https: an agent that would clone any URL a message contained is a
way to make the platform fetch from inside its own network.

ONE CHECKOUT PER SCOPE. On a project, the clone IS the legacy code of the turn's
scope (`modernization_common.legacy_code.current_scope`): the project's checkout on a
page's chat — the one "Pull legacy code" fills and Requirements reads — or, on an
Orchestrator turn, that conversation's own copy. Asked for the repository already pulled
in that scope, this tool adopts it instead of cloning again; asked for another one, its
clone replaces it. Only with no project bound (tests, scripts) does it clone into a
conversation-private folder.
"""
from __future__ import annotations

import asyncio
import json
import os
import pathlib
import subprocess
import urllib.parse
from dataclasses import dataclass, field
from typing import Optional

from langchain_core.tools import tool

from config.ws_helper import get_session_id, get_user_id

#: Hosts a bare https clone URL may name.
_ALLOWED_HOSTS = frozenset({"github.com", "dev.azure.com", "gitlab.com", "bitbucket.org"})
_CLONE_TIMEOUT_SECONDS = 300
_DISABLED_PUSH_URL = "DISCOVERY-IS-READ-ONLY"
# Never consult a system credential helper (Windows GCM hijacks dev.azure.com with a
# stale credential) and never prompt (it would hang a headless worker).
_GIT_NO_HELPER = ["-c", "credential.helper=", "-c", "credential.useHttpPath=true"]


@dataclass
class DiscoverySession:
    work_dir: str = ""
    repo_url: str = ""
    repo_name: str = ""
    branch: str = ""
    commit: str = ""
    provider: str = ""
    repos: dict[str, str] = field(default_factory=dict)  # name -> clone url
    assessment: Optional[dict] = None


_SESSIONS: dict[str, DiscoverySession] = {}


def session(session_id: str | None = None) -> DiscoverySession:
    key = str(session_id or get_session_id() or "default")
    return _SESSIONS.setdefault(key, DiscoverySession())


def _git_env() -> dict:
    return {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never"}


def _files_root() -> str:
    from config import sdlcSettings  # noqa: PLC0415

    return sdlcSettings().FILES


def legacy_checkout_dir() -> str:
    return os.path.join(
        _files_root(), str(get_user_id() or "anonymous"), "discovery_agent",
        str(get_session_id() or "session"), "legacy",
    )


def validate_clone_url(url: str) -> str | None:
    """None when `url` may be cloned; otherwise why not, in words the agent can repeat."""
    parsed = urllib.parse.urlparse((url or "").strip())
    if parsed.scheme != "https":
        return "Only https clone URLs are accepted."
    host = (parsed.hostname or "").lower()
    if host in _ALLOWED_HOSTS or host.endswith(".visualstudio.com"):
        return None
    return (
        f"'{host or url}' is not a repository host Discovery may clone from. Supported: "
        "Azure DevOps, GitHub, GitLab and Bitbucket — or wire the project's repository "
        "connector to the Dependency and Risk stage."
    )


def _inject(url: str, secret: str) -> str:
    if not secret:
        return url
    parsed = urllib.parse.urlparse(url)
    user = "PersonalAccessToken" if "azure" in (parsed.hostname or "") or \
        (parsed.hostname or "").endswith(".visualstudio.com") else "x-access-token"
    netloc = f"{user}:{urllib.parse.quote(secret, safe='')}@{parsed.hostname}"
    if parsed.port:
        netloc += f":{parsed.port}"
    return urllib.parse.urlunparse(parsed._replace(netloc=netloc))


def _scrub(text: str, secret: str) -> str:
    if not secret:
        return text
    return text.replace(secret, "***").replace(urllib.parse.quote(secret, safe=""), "***")


async def _stage_credentials() -> tuple[str, str, str, str]:
    """(provider, org_url, secret, refusal) from the connector bound to this stage.

    `provider` is "ado", "github" or "" (nothing bound). `refusal` is non-empty when a
    connector IS bound but this stage may not read through it.
    """
    try:
        from config.connectors.context import get_connector  # noqa: PLC0415

        conn = get_connector()
    except Exception:  # noqa: BLE001 — nothing bound for this turn
        return "", "", "", ""
    level = getattr(conn, "access_level", "unscoped")
    if level != "unscoped":
        from shared.authz.connector_access import permits  # noqa: PLC0415

        if not permits(level, "read"):
            return "", "", "", (
                f"The {getattr(conn, 'display_name', 'repository')} connection is not readable "
                "by the Dependency and Risk stage on this project. A Project Admin can wire "
                "it to this stage with read access in project settings."
            )
    try:
        auth = await conn.auth_adapter()
    except Exception as exc:  # noqa: BLE001
        return "", "", "", f"The repository connection could not be authenticated ({type(exc).__name__})."
    name = str(getattr(conn, "connector_name", "") or "")
    secret = auth.get("pat") or auth.get("token") or ""
    provider = "github" if "github" in name else "ado"
    return provider, str(auth.get("org_url") or "").rstrip("/"), secret, ""


@tool
async def list_legacy_repositories(project: str = "") -> str:
    """List the repositories the project's repository connection can see.

    Azure DevOps: call with no argument to list ADO projects, then with the chosen
    project's name to list its repositories. GitHub: lists repositories directly.
    Show the list and ask which one holds the legacy system — never guess.
    """
    provider, org_url, secret, refusal = await _stage_credentials()
    if refusal:
        return refusal
    if not secret:
        return (
            "No repository connection is available to the Dependency and Risk stage on this "
            "project. A Project Admin can wire Azure DevOps or GitHub to this stage in project "
            "settings. Alternatively, give me a public https clone URL (Azure DevOps, GitHub, "
            "GitLab or Bitbucket)."
        )
    s = session()
    try:
        if provider == "github":
            from shared.services import github_repos  # noqa: PLC0415

            owners = await github_repos.list_projects(token=secret)
            repos: list[dict] = []
            for owner in owners:
                if project and owner["name"].lower() != project.lower():
                    continue
                for r in await github_repos.list_repos(owner["name"], token=secret):
                    repos.append({**r, "name": f"{owner['name']}/{r['name']}"})
        else:
            from shared.services import ado_repos  # noqa: PLC0415

            if not project:
                projects = await ado_repos.list_projects(pat=secret, org_url=org_url)
                if not projects:
                    return "No Azure DevOps projects are visible to this connection."
                lines = [f"{i + 1}. {p['name']}" for i, p in enumerate(projects)]
                return ("Azure DevOps projects:\n" + "\n".join(lines)
                        + "\n\nWhich project holds the legacy system? Then I will list its repositories.")
            repos = await ado_repos.list_repos(project, pat=secret, org_url=org_url)
    except Exception as exc:  # noqa: BLE001
        return f"Listing repositories failed: {_scrub(str(exc), secret)[:300]}"

    if not repos:
        return f"No repositories found{f' in {project}' if project else ''}."
    for r in repos:
        s.repos[r["name"]] = r.get("remote_url") or ""
    lines = [
        f"{i + 1}. {r['name']}" + (f"  (default branch: {r['default_branch'].replace('refs/heads/', '')})"
                                   if r.get("default_branch") else "")
        for i, r in enumerate(repos)
    ]
    return ("Repositories:\n" + "\n".join(lines)
            + "\n\nWhich repository is the legacy system to assess? Reply with its number or name.")


def _clone(url: str, dest: str, branch: str, secret: str) -> dict:
    """Shallow, single-branch clone of `url` into `dest`, then disable pushing.
    Returns {"commit", "branch"}; raises RuntimeError with the secret scrubbed."""
    target = pathlib.Path(dest)
    if target.exists():
        from shared.services.ado_repos import _force_rmtree  # noqa: PLC0415

        _force_rmtree(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    args = ["git", *_GIT_NO_HELPER, "clone", "--depth", "1", "--single-branch"]
    if branch:
        args += ["--branch", branch]
    result = subprocess.run(
        args + [_inject(url, secret), str(target)], capture_output=True, text=True,
        timeout=_CLONE_TIMEOUT_SECONDS, env=_git_env(),
    )
    if result.returncode != 0:
        raise RuntimeError(_scrub((result.stderr or result.stdout).strip(), secret)[:500])

    def git(*a: str) -> str:
        out = subprocess.run(["git", *a], cwd=str(target), capture_output=True, text=True,
                             timeout=30, env=_git_env())
        return out.stdout.strip()

    # Read-only from here on: the remote keeps its fetch URL (credential-free) and
    # loses its push URL.
    git("remote", "set-url", "origin", url)
    git("remote", "set-url", "--push", "origin", _DISABLED_PUSH_URL)
    return {"commit": git("rev-parse", "HEAD"), "branch": git("rev-parse", "--abbrev-ref", "HEAD")}


@tool
async def clone_legacy_repository(repository: str, branch: str = "") -> str:
    """Clone the legacy repository READ-ONLY so it can be assessed.

    Args:
        repository: A name from list_legacy_repositories, or a full https clone URL.
        branch: Optional branch; omit for the repository's default branch.
    """
    s = session()
    url = (repository or "").strip()
    if not url.lower().startswith("https://"):
        match = next((u for n, u in s.repos.items() if n.lower() == url.lower()), None)
        if not match:
            return (f"I don't know a repository called '{repository}'. Call "
                    "list_legacy_repositories first, or pass a full https clone URL.")
        url = match
    problem = validate_clone_url(url)
    if problem:
        return problem

    # A connection problem (unreadable stage, credentials that do not authenticate) only
    # withholds the CREDENTIAL. A public repository needs none, so the clone still goes
    # ahead without one, and the problem is reported only if the clone then fails —
    # refusing a public clone because an unrelated GitHub connection is misconfigured
    # would block the assessment for no reason.
    provider, _org, secret, problem = await _stage_credentials()
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    host_provider = "github" if host == "github.com" else (
        "ado" if host == "dev.azure.com" or host.endswith(".visualstudio.com") else "public")
    use_secret = secret if provider and provider == host_provider else ""

    from agents_orchestrator.modernization_common.legacy_code import current_scope  # noqa: PLC0415

    project_id, run_id = current_scope()
    if project_id:
        return await _clone_for_project(s, project_id, url, branch.strip(), use_secret, problem, run_id)

    dest = legacy_checkout_dir()
    try:
        facts = await asyncio.to_thread(_clone, url, dest, branch.strip(), use_secret)
    except subprocess.TimeoutExpired:
        return f"Cloning timed out after {_CLONE_TIMEOUT_SECONDS} seconds."
    except Exception as exc:  # noqa: BLE001
        hint = f" Note: {problem}" if problem else (
            "" if use_secret else " No credential was used — if the repository is private, "
            "wire its connection to the Dependency and Risk stage in project settings.")
        return f"Clone failed: {exc}.{hint}"

    clean_url = urllib.parse.urlunparse(urllib.parse.urlparse(url)._replace(
        netloc=urllib.parse.urlparse(url).hostname or ""))
    s.work_dir, s.repo_url, s.provider = dest, clean_url, host_provider
    s.repo_name = clean_url.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git")
    s.branch, s.commit = facts["branch"], facts["commit"]
    s.assessment = None
    files = 0
    for _dirpath, dirnames, filenames in os.walk(dest):
        dirnames[:] = [d for d in dirnames if d != ".git"]
        files += len(filenames)
    return json.dumps({
        "cloned": True, "read_only": True, "repository": clean_url, "branch": s.branch,
        "commit": s.commit[:12], "files": files,
        "next": "Call assess_legacy_repository to build the assessment.",
    })


def _same_repo(a: str, b: str) -> bool:
    def norm(u: str) -> str:
        return (u or "").strip().rstrip("/").removesuffix(".git").lower()
    return norm(a) == norm(b)


def adopt_project_checkout(s: DiscoverySession, project_id: str, pull: dict,
                           run_id: str | None = None) -> None:
    """Point this conversation at the pulled legacy code of its scope."""
    from agents_orchestrator.modernization_common.legacy_code import checkout_dir  # noqa: PLC0415

    s.work_dir = str(checkout_dir(project_id, run_id))
    s.repo_url, s.provider = pull.get("url", ""), pull.get("provider", "")
    s.repo_name, s.branch, s.commit = pull.get("name", ""), pull.get("branch", ""), pull.get("commit", "")
    s.assessment = None


async def _clone_for_project(s: DiscoverySession, project_id: str, url: str, branch: str,
                             secret: str, problem: str, run_id: str | None = None) -> str:
    from agents_orchestrator.modernization_common import legacy_code  # noqa: PLC0415

    where = "in this conversation" if run_id else "for the project"
    pull = legacy_code.current_pull(project_id, run_id)
    if pull and _same_repo(pull.get("url", ""), url) and (not branch or branch == pull.get("branch")):
        adopt_project_checkout(s, project_id, pull, run_id)
        return json.dumps({
            "cloned": False, "reused": True, "read_only": True, "repository": pull.get("url"),
            "branch": s.branch, "commit": s.commit[:12],
            "note": f"This repository is already pulled {where} — using that checkout.",
            "next": "Call assess_legacy_repository to build the assessment.",
        })
    record = await legacy_code.pull_now(
        project_id, url, branch, user_id=str(get_user_id() or ""), secret=secret, problem=problem,
        run_id=run_id)
    if record.get("status") != "ready":
        return f"Clone failed: {record.get('error') or 'unknown error'}"
    adopt_project_checkout(s, project_id, record["pull"], run_id)
    summary = (record["pull"].get("profile") or {}).get("summary") or {}
    return json.dumps({
        "cloned": True, "read_only": True, "repository": s.repo_url, "branch": s.branch,
        "commit": s.commit[:12], "files": summary.get("files"),
        "note": f"This is now the legacy code {where} — the Requirements agent reads the same checkout.",
        "next": "Call assess_legacy_repository to build the assessment.",
    })


async def repositories_data(ado_project: str = "") -> dict:
    """What the connection bound for this call can see, as data for the pages' picker:
    `{"provider", "projects"}` (Azure DevOps, no project chosen yet), `{"provider",
    "repositories": [{"name", "url", "defaultBranch"}]}`, or `{"provider", "problem"}`.
    The agent's `list_legacy_repositories` stays the conversational version of this."""
    provider, org_url, secret, refusal = await _stage_credentials()
    if refusal:
        return {"provider": provider, "problem": refusal}
    if not secret:
        return {"provider": "", "problem": ""}
    try:
        if provider == "github":
            from shared.services import github_repos  # noqa: PLC0415

            repos: list[dict] = []
            for owner in await github_repos.list_projects(token=secret):
                if ado_project and owner["name"].lower() != ado_project.lower():
                    continue
                for r in await github_repos.list_repos(owner["name"], token=secret):
                    repos.append({**r, "name": f"{owner['name']}/{r['name']}"})
        else:
            from shared.services import ado_repos  # noqa: PLC0415

            if not ado_project:
                projects = await ado_repos.list_projects(pat=secret, org_url=org_url)
                return {"provider": "ado", "projects": [p["name"] for p in projects]}
            repos = await ado_repos.list_repos(ado_project, pat=secret, org_url=org_url)
    except Exception as exc:  # noqa: BLE001
        return {"provider": provider, "problem": f"Listing repositories failed: {_scrub(str(exc), secret)[:300]}"}
    return {
        "provider": provider,
        "repositories": [
            {"name": r["name"], "url": r.get("remote_url") or "",
             "defaultBranch": (r.get("default_branch") or "").replace("refs/heads/", "")}
            for r in repos
        ],
    }
