"""Shared helper for ADO projects / repos / branches — consumed by the dev-workspace
REST endpoints and the development-agent git tools (DRY source).

Credentials are PER-TENANT and resolve through resolve_auth() below: the connector
bound to the running stage first, then an explicit tenant_id. There is no process-wide
ADO_ORG_URL / ADO_PAT fallback — a platform-wide PAT would let a tenant that never
connected Azure DevOps transact as the platform, against another tenant's projects.
"""
from __future__ import annotations

import base64
import os
import pathlib
import re
import shutil
import stat
import subprocess
import urllib.parse

import httpx

from config.env import DEV_WORKSPACE_ROOT

WORKSPACE_ROOT = pathlib.Path(DEV_WORKSPACE_ROOT)

_API_VER = "api-version=7.0"
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
)

# Force git to authenticate with ONLY the PAT embedded in the remote URL. On Windows,
# Git Credential Manager otherwise intercepts dev.azure.com and replaces the URL's PAT
# with a stale cached credential (or an Azure-AD OAuth attempt), which the server
# rejects as 'Authentication failed' even though the PAT is valid (and works over REST).
# Disabling the helper + useHttpPath makes git use the URL credential verbatim.
_GIT_NO_HELPER = ["-c", "credential.helper=", "-c", "credential.useHttpPath=true"]


def _git_env() -> dict:
    """Env for git subprocesses: never fall back to an interactive credential prompt
    (which hangs a headless server or masquerades as an auth failure)."""
    return {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never"}


def _auth_header(pat: str | None = None) -> dict[str, str]:
    token = base64.b64encode(f":{pat or ''}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


async def _get_json(url: str, pat: str | None = None) -> dict:
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.get(url, headers=_auth_header(pat))
        r.raise_for_status()
        return r.json()


async def resolve_auth(tenant_id: str = "") -> tuple[str, str]:
    """Resolve (org_url, pat) for Azure DevOps for THIS TENANT.

    Two tenant-scoped sources, in order:
      1. the connector bound to the running stage (config.connectors.context) — this
         is the path a pipeline run takes, and it carries the run's tenant;
      2. an explicit tenant_id, for callers outside a stage (REST endpoints).

    Returns ("", "") when neither answers. Never raises and never falls back to a
    process-wide credential: callers surface a clean "not configured" instead.
    """
    try:
        from config.connectors.context import get_connector as _active_connector

        active = _active_connector()
        # The bound connector is whatever THIS stage needs — often Jira or GitHub.
        # Only an ADO one can answer for ADO; asking any other kind would read a
        # "pat" key it does not have and silently return an empty credential.
        if active.connector_name in ("azure_devops", "azure_repos"):
            auth = await active.auth_adapter()
            org, pat = (auth.get("org_url") or "").rstrip("/"), auth.get("pat") or ""
            if pat:
                return org, pat
    except Exception:  # noqa: BLE001 — no connector bound to this run
        pass

    if tenant_id:
        try:
            from config.connector_factory import get_connector_for_session

            # unrestricted: this resolves CREDENTIALS via auth_adapter, which is
            # neither a read nor a write of connector data and is never gated. The
            # operations performed with those credentials are gated where they happen.
            conn = await get_connector_for_session(
                "azure_devops", tenant_id=tenant_id, unrestricted=True,
            )
            auth = await conn.auth_adapter(tenant_id)
            return (auth.get("org_url") or "").rstrip("/"), auth.get("pat") or ""
        except Exception:  # noqa: BLE001
            pass

    return "", ""


async def _resolve(org_url: str | None, pat: str | None, tenant_id: str) -> tuple[str, str]:
    """Return (base_url, pat). Explicit args win; otherwise resolve from this
    tenant's connector (Integrations creds). Raises if no org URL is configured so
    the endpoint returns a clear message instead of a malformed-URL 500."""
    if not (org_url and pat):
        c_org, c_pat = await resolve_auth(tenant_id)
        org_url = org_url or c_org
        pat = pat or c_pat
    base = (org_url or "").rstrip("/")
    if not base:
        raise RuntimeError(
            "Azure DevOps is not configured. Connect Azure DevOps on the Integrations page."
        )
    return base, (pat or "")


async def list_projects(
    pat: str | None = None,
    org_url: str | None = None,
    tenant_id: str = "",
) -> list[dict]:
    """Return all ADO projects as [{id, name}].

    Credentials resolve from the connector (Integrations page) unless *pat* /
    *org_url* are passed explicitly (e.g. a per-session PAT from the agent).
    """
    base, pat = await _resolve(org_url, pat, tenant_id)
    data = await _get_json(f"{base}/_apis/projects?{_API_VER}", pat=pat)
    return [{"id": p["id"], "name": p["name"]} for p in data.get("value", [])]


async def list_repos(
    project: str,
    pat: str | None = None,
    org_url: str | None = None,
    tenant_id: str = "",
) -> list[dict]:
    """Return all Git repos in *project* as [{id, name, default_branch, remote_url}].

    Credentials resolve from the connector unless *pat* / *org_url* are explicit.
    """
    base, pat = await _resolve(org_url, pat, tenant_id)
    data = await _get_json(
        f"{base}/{urllib.parse.quote(project, safe='')}/_apis/git/repositories?{_API_VER}",
        pat=pat,
    )
    return [
        {
            "id": r["id"],
            "name": r["name"],
            "default_branch": r.get("defaultBranch", ""),
            "remote_url": r.get("remoteUrl", ""),
        }
        for r in data.get("value", [])
    ]


async def list_branches(
    project: str,
    repo_id_or_name: str,
    pat: str | None = None,
    org_url: str | None = None,
    tenant_id: str = "",
) -> list[dict]:
    """Return all branches in a repo as [{name, is_default}].

    *repo_id_or_name* may be:
    - A UUID (8-4-4-4-12 hex): used directly as the repo id.
    - A repo name: resolved to its id via list_repos(project).

    `is_default` is True when the ref matches the repo's defaultBranch value.
    When a UUID is supplied directly we have no defaultBranch context, so
    is_default is always False.

    *pat* and *org_url* override the module-level env vars when provided.
    """
    default_branch: str = ""
    base, pat = await _resolve(org_url, pat, tenant_id)

    if _UUID_RE.match(repo_id_or_name):
        repo_id = repo_id_or_name
    else:
        repos = await list_repos(project, pat=pat, org_url=base)
        matched = next((r for r in repos if r["name"] == repo_id_or_name), None)
        if matched is None:
            return []
        repo_id = matched["id"]
        default_branch = matched["default_branch"]

    encoded_project = urllib.parse.quote(project, safe="")
    data = await _get_json(
        f"{base}/{encoded_project}/_apis/git/repositories/"
        f"{repo_id}/refs?filter=heads&{_API_VER}",
        pat=pat,
    )

    result = []
    for ref in data.get("value", []):
        raw = ref.get("name", "")
        if not raw.startswith("refs/heads/"):
            continue
        name = raw.removeprefix("refs/heads/")
        result.append({"name": name, "is_default": raw == default_branch})
    return result


async def resolve_clone_url(
    project: str,
    repo_name: str,
    pat: str | None = None,
    org_url: str | None = None,
    tenant_id: str = "",
) -> str | None:
    """Return the HTTPS remote URL for *repo_name* in *project*, or None if not found."""
    repos = await list_repos(project, pat=pat, org_url=org_url, tenant_id=tenant_id)
    matched = next((r for r in repos if r["name"] == repo_name), None)
    return matched["remote_url"] if matched else None


async def _resolve_repo_id(
    project: str, repo_id_or_name: str, base: str, pat: str
) -> str | None:
    """Return the repo id for a name or pass a UUID straight through."""
    if _UUID_RE.match(repo_id_or_name):
        return repo_id_or_name
    repos = await list_repos(project, pat=pat, org_url=base)
    matched = next((r for r in repos if r["name"] == repo_id_or_name), None)
    return matched["id"] if matched else None


async def list_pull_requests(
    project: str,
    repo_id_or_name: str,
    status: str = "active",
    pat: str | None = None,
    org_url: str | None = None,
    tenant_id: str = "",
) -> list[dict]:
    """Return open PRs for a repo as
    [{id, title, source_branch, target_branch, created_by}]."""
    base, pat = await _resolve(org_url, pat, tenant_id)
    repo_id = await _resolve_repo_id(project, repo_id_or_name, base, pat)
    if not repo_id:
        return []
    encoded_project = urllib.parse.quote(project, safe="")
    data = await _get_json(
        f"{base}/{encoded_project}/_apis/git/repositories/{repo_id}/pullrequests"
        f"?searchCriteria.status={status}&{_API_VER}",
        pat=pat,
    )
    out = []
    for pr in data.get("value", []):
        out.append({
            "id": str(pr.get("pullRequestId", "")),
            "title": pr.get("title", ""),
            "source_branch": (pr.get("sourceRefName", "") or "").removeprefix("refs/heads/"),
            "target_branch": (pr.get("targetRefName", "") or "").removeprefix("refs/heads/"),
            "created_by": (pr.get("createdBy") or {}).get("displayName", ""),
        })
    return out


async def get_pull_request(
    project: str,
    repo_id_or_name: str,
    pr_id: str,
    pat: str | None = None,
    org_url: str | None = None,
    tenant_id: str = "",
) -> dict | None:
    """Return one PR's {id, title, source_branch, target_branch} or None."""
    base, pat = await _resolve(org_url, pat, tenant_id)
    repo_id = await _resolve_repo_id(project, repo_id_or_name, base, pat)
    if not repo_id:
        return None
    encoded_project = urllib.parse.quote(project, safe="")
    pr = await _get_json(
        f"{base}/{encoded_project}/_apis/git/repositories/{repo_id}"
        f"/pullrequests/{pr_id}?{_API_VER}",
        pat=pat,
    )
    return {
        "id": str(pr.get("pullRequestId", pr_id)),
        "title": pr.get("title", ""),
        "source_branch": (pr.get("sourceRefName", "") or "").removeprefix("refs/heads/"),
        "target_branch": (pr.get("targetRefName", "") or "").removeprefix("refs/heads/"),
    }


def clone_and_diff(
    work_dir: str, remote_url: str, source: str, base: str, pat: str, max_bytes: int = 200_000
) -> dict:
    """Clone *remote_url* read-only and compute the diff of *source* vs *base*.

    Returns {diff, files:[{path,status,added,removed}], head_sha, base_sha,
    truncated}. Raises RuntimeError (PAT scrubbed) on a genuine clone failure.
    The diff is `base...source` (three-dot: changes introduced on source since the
    merge-base), capped at *max_bytes*.
    """
    work_path = pathlib.Path(work_dir)
    auth_url = _inject_pat(remote_url, pat)

    def _git(args: list[str], timeout: int = 300) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", *_GIT_NO_HELPER, *args], cwd=str(work_path), capture_output=True,
            text=True, timeout=timeout, encoding="utf-8", errors="replace", env=_git_env(),
        )

    if work_path.exists():
        _force_rmtree(work_path)
    work_path.mkdir(parents=True, exist_ok=True)

    clone = _git(["clone", "--no-single-branch", auth_url, "."])
    if clone.returncode != 0:
        raw = clone.stderr.strip() or clone.stdout.strip()
        raise RuntimeError(_scrub(raw, pat))

    # Make sure both refs are present locally.
    _git(["fetch", "origin", source, base], timeout=120)
    rng = f"origin/{base}...origin/{source}"

    head_sha = (_git(["rev-parse", f"origin/{source}"], timeout=30).stdout or "").strip()
    base_sha = (_git(["merge-base", f"origin/{base}", f"origin/{source}"], timeout=30).stdout or "").strip()

    name_status = _git(["diff", "--name-status", "-M", rng], timeout=120)
    numstat = _git(["diff", "--numstat", rng], timeout=120)
    counts: dict[str, tuple[int, int]] = {}
    for ln in (numstat.stdout or "").splitlines():
        parts = ln.split("\t")
        if len(parts) >= 3:
            a = int(parts[0]) if parts[0].isdigit() else 0
            r = int(parts[1]) if parts[1].isdigit() else 0
            counts[parts[2]] = (a, r)

    files = []
    for ln in (name_status.stdout or "").splitlines():
        parts = ln.split("\t")
        if len(parts) < 2:
            continue
        status = parts[0][:1]
        path = parts[-1]
        a, r = counts.get(path, (0, 0))
        files.append({"path": path, "status": status, "added": a, "removed": r})

    full = _git(["diff", "--unified=3", "-M", rng], timeout=180)
    diff_text = full.stdout or ""
    truncated = False
    if len(diff_text.encode("utf-8", "ignore")) > max_bytes:
        diff_text = diff_text.encode("utf-8", "ignore")[:max_bytes].decode("utf-8", "ignore")
        truncated = True

    return {
        "diff": diff_text,
        "files": files,
        "head_sha": head_sha,
        "base_sha": base_sha,
        "truncated": truncated,
    }


def _inject_pat(url: str, pat: str) -> str:
    """Insert PAT credentials into an HTTPS URL: https://PersonalAccessToken:{pat}@host/...

    A NON-EMPTY username is required. Newer (84-char) Azure DevOps PATs reject Basic
    auth when the username is empty — the classic `https://:{pat}@` form fails clone
    with 'fatal: Authentication failed' even though the same token authenticates over
    REST (which sends `base64(":pat")`). Any non-empty username with the PAT as the
    password works, so we use a fixed placeholder. The PAT is also percent-encoded so
    URL-reserved characters can't mangle the netloc.
    """
    if not url.startswith("https://"):
        raise ValueError("Only HTTPS remote URLs are supported")
    parsed = urllib.parse.urlparse(url)
    safe_pat = urllib.parse.quote(pat, safe="")
    authed = parsed._replace(
        netloc=f"PersonalAccessToken:{safe_pat}@{parsed.hostname}{f':{parsed.port}' if parsed.port else ''}")
    return urllib.parse.urlunparse(authed)


def _scrub(text: str, pat: str) -> str:
    """Redact both the raw and URL-encoded PAT from git output before it is raised
    or logged (the clone URL carries the percent-encoded form, error text may echo
    either)."""
    if not pat:
        return text
    return text.replace(pat, "***").replace(urllib.parse.quote(pat, safe=""), "***")


def _force_rmtree(path: pathlib.Path) -> None:
    """Remove a directory tree, clearing the Windows read-only bit on git
    pack/object files that a plain rmtree(ignore_errors=True) leaves behind
    (which then makes `git clone .` fail with 'destination path not empty')."""
    def _onexc(func, p, _exc):
        try:
            os.chmod(p, stat.S_IWRITE)
            func(p)
        except OSError:
            pass

    try:
        shutil.rmtree(path, onexc=_onexc)  # Python 3.12+
    except TypeError:
        shutil.rmtree(path, onerror=lambda f, p, _e: _onexc(f, p, None))


def clone_into(work_dir: str, remote_url: str, branch: str, pat: str) -> dict:
    """Clone *remote_url* into *work_dir* on *branch*, replacing any existing clone.

    If *branch* doesn't exist (empty/new repo), clones the default and creates the
    branch so the agent can start working — an empty repo has no commit, so
    commit_sha comes back "" in that case.

    Returns {"commit_sha": <sha or "">}. Raises RuntimeError (PAT scrubbed) on a
    genuine clone failure (bad URL / auth / network).
    """
    work_path = pathlib.Path(work_dir)
    auth_url = _inject_pat(remote_url, pat)

    def _git(args: list[str], timeout: int = 300) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", *_GIT_NO_HELPER, *args], cwd=str(work_path), capture_output=True,
            text=True, timeout=timeout, env=_git_env(),
        )

    def _fresh_dir() -> None:
        if work_path.exists():
            _force_rmtree(work_path)
        work_path.mkdir(parents=True, exist_ok=True)

    _fresh_dir()
    clone_result = _git(["clone", "--branch", branch, auth_url, "."])

    if clone_result.returncode != 0:
        # The requested branch may not exist (empty or brand-new repo). Clone the
        # default branch (or an empty repo) and create/checkout the branch locally.
        _fresh_dir()
        plain = _git(["clone", auth_url, "."])
        if plain.returncode != 0:
            raw = plain.stderr.strip() or plain.stdout.strip()
            raise RuntimeError(_scrub(raw, pat))
        # Create the branch (or stay on it if the default already matches).
        if _git(["checkout", "-b", branch], timeout=30).returncode != 0:
            _git(["checkout", branch], timeout=30)

    sha_result = _git(["rev-parse", "HEAD"], timeout=30)
    commit_sha = sha_result.stdout.strip() if sha_result.returncode == 0 else ""
    return {"commit_sha": commit_sha}


def commit_and_push_files(
    work_dir: str, base_branch: str, new_branch: str,
    files: list[tuple[str, str]], pat: str, commit_message: str,
) -> str:
    """In an existing clone at *work_dir*: create *new_branch* off *base_branch*,
    write *files* (repo-relative path, contents), commit, and push. Returns the
    pushed commit sha. Raises RuntimeError (PAT scrubbed) on failure."""
    work_path = pathlib.Path(work_dir)

    def _git(args: list[str], timeout: int = 180) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", *_GIT_NO_HELPER, *args], cwd=str(work_path), capture_output=True,
            text=True, timeout=timeout, encoding="utf-8", errors="replace", env=_git_env(),
        )

    # Identity (push/commit needs one in CI-like envs).
    _git(["config", "user.email", "deployment-agent@sdlc.local"])
    _git(["config", "user.name", "SDLC Deployment Agent"])
    if _git(["checkout", "-b", new_branch], timeout=30).returncode != 0:
        _git(["checkout", new_branch], timeout=30)

    for rel, contents in files:
        target = (work_path / rel).resolve()
        try:
            target.relative_to(work_path.resolve())
        except ValueError:
            continue  # traversal guard
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(contents, encoding="utf-8")

    _git(["add", "-A"])
    commit = _git(["commit", "-m", commit_message])
    if commit.returncode != 0 and "nothing to commit" not in (commit.stdout + commit.stderr).lower():
        raise RuntimeError(_scrub(commit.stderr or commit.stdout, pat))
    push = _git(["push", "--set-upstream", "origin", new_branch], timeout=180)
    if push.returncode != 0:
        raise RuntimeError(_scrub(push.stderr or push.stdout, pat))
    sha = _git(["rev-parse", "HEAD"], timeout=30)
    return sha.stdout.strip() if sha.returncode == 0 else ""


async def create_pull_request(
    project: str, repo_id_or_name: str, source_branch: str, target_branch: str,
    title: str, description: str = "", pat: str | None = None,
    org_url: str | None = None, tenant_id: str = "",
) -> str | None:
    """Open an ADO pull request; returns the web URL or None."""
    base, pat = await _resolve(org_url, pat, tenant_id)
    repo_id = await _resolve_repo_id(project, repo_id_or_name, base, pat)
    if not repo_id:
        return None
    encoded_project = urllib.parse.quote(project, safe="")
    url = (f"{base}/{encoded_project}/_apis/git/repositories/{repo_id}"
           f"/pullrequests?{_API_VER}")
    body = {
        "sourceRefName": f"refs/heads/{source_branch}",
        "targetRefName": f"refs/heads/{target_branch}",
        "title": title[:400],
        "description": description[:4000],
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(url, headers={**_auth_header(pat), "Content-Type": "application/json"}, json=body)
        r.raise_for_status()
        data = r.json()
    pr_id = data.get("pullRequestId")
    if not pr_id:
        return None
    return f"{base}/{encoded_project}/_git/{urllib.parse.quote(repo_id_or_name, safe='')}/pullrequest/{pr_id}"
