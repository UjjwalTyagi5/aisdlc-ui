"""GitHub as a source of repositories, in the shape `ado_repos` already answers in.

WHY THIS EXISTS. Every workspace on this platform — documentation, deployment,
development, testing, code review — clones through `shared/services/ado_repos`, which
talks to Azure DevOps and nothing else. A tenant whose code is on GitHub could connect
GitHub Issues and GitHub Actions and still not open a workspace, because no connector in
this codebase implements cloning at all: `ado_repos` is a service, not a connector, and
it was the only one.

THE SHAPES ARE DELIBERATELY IDENTICAL to `ado_repos`, so `repo_source` can dispatch
between the two without either caller or façade knowing which is which. Where the two
providers genuinely differ, this module translates rather than inventing:

  · ADO nests repos under a PROJECT. GitHub has no such layer — the equivalent
    namespace is the OWNER (an organisation, or the authenticated user). `list_projects`
    therefore returns owners. Calling it "project" in the façade keeps one vocabulary at
    the call sites; pretending GitHub has projects would be the lie.
  · ADO numbers pull requests per repository and calls the field `pullRequestId`;
    GitHub calls it `number`. Both are surfaced as `id`.
  · ADO branch refs arrive as `refs/heads/main`; GitHub returns the bare name. Both are
    normalised to the bare name, because that is what `clone_into` wants.

THE TOKEN IS A GITHUB APP INSTALLATION TOKEN, resolved through the same connector
factory the rest of the platform uses, and it is short-lived by design. Nothing here
caches it beyond the call.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_API = "https://api.github.com"
_TIMEOUT = 30.0
#: GitHub caps a page at 100; a tenant with more repos than this needs paging, and
#: silently returning the first hundred as if they were all of them is how somebody
#: concludes their repository does not exist.
_PER_PAGE = 100


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


async def _get(path: str, token: str, params: dict | None = None) -> Any:
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        r = await client.get(f"{_API}{path}", headers=_headers(token), params=params or {})
        r.raise_for_status()
        return r.json()


async def _paged(path: str, token: str, params: dict | None = None) -> list[dict]:
    """Every page, not just the first.

    A tenant with 120 repositories seeing 100 concludes the missing 20 were deleted.
    Capped so a pathological account cannot hang a page load.
    """
    out: list[dict] = []
    page = 1
    while page <= 10:
        batch = await _get(path, token, {**(params or {}), "per_page": _PER_PAGE, "page": page})
        if not isinstance(batch, list) or not batch:
            break
        out.extend(batch)
        if len(batch) < _PER_PAGE:
            break
        page += 1
    return out


async def resolve_auth(
    tenant_id: str = "", *, project_id: str = "", owner_id: str = ""
) -> tuple[str, str]:
    """(api_base, token) for GitHub, or ("", "") when this tenant has no GitHub.

    Mirrors `ado_repos.resolve_auth` including the project/owner scoping, so a
    per-user, per-project credential is found where one exists. Never raises: a tenant
    without GitHub is an ordinary state, and callers report "not configured" themselves.
    """
    if not tenant_id:
        return "", ""
    for kind in ("github_issues", "github_actions"):
        try:
            from config.connector_factory import get_connector_for_session  # noqa: PLC0415

            conn = await get_connector_for_session(
                kind, tenant_id=tenant_id, unrestricted=True,
                project_id=project_id, owner_id=owner_id,
            )
            auth = await conn.auth_adapter(tenant_id)
            token = auth.get("token") or ""
            if token:
                return _API, token
        except Exception:  # noqa: BLE001 — this kind is simply not connected
            continue
    return "", ""


async def _token(token: str | None, tenant_id: str, project_id: str, owner_id: str) -> str:
    if token:
        return token
    _base, resolved = await resolve_auth(
        tenant_id, project_id=project_id, owner_id=owner_id)
    if not resolved:
        raise RuntimeError(
            "GitHub is not configured. Connect GitHub on the Integrations page."
        )
    return resolved


async def _visible_repos(token: str) -> list[dict]:
    """Every repository this credential can see, whatever KIND of credential it is.

    GitHub hands out two things this platform may hold, and they are not
    interchangeable:

      · a GitHub App INSTALLATION token, which answers `/installation/repositories`
        and 403s on `/user/repos` — it belongs to an app, not a person;
      · a personal access token (classic or fine-grained), which is the opposite.

    Asking the wrong one produces a 403 whose message names the other, which is exactly
    what a first implementation assuming App tokens hit against a tenant using a
    fine-grained PAT. Trying the installation route first and falling back keeps both
    working without asking anybody to declare which they configured — and a caller who
    has neither still ends with an empty list rather than an exception, because "no
    repositories" is the honest answer to "what can this token see".
    """
    try:
        data = await _get("/installation/repositories", token, {"per_page": _PER_PAGE})
        if isinstance(data, dict) and "repositories" in data:
            return list(data.get("repositories") or [])
    except Exception:  # noqa: BLE001 — not an installation token; try the user route
        pass
    try:
        return await _paged(
            "/user/repos", token, {"affiliation": "owner,organization_member"})
    except Exception as exc:  # noqa: BLE001
        logger.warning("github: no repositories readable (%s)", type(exc).__name__)
        return []


async def list_projects(
    token: str | None = None, tenant_id: str = "",
    *, project_id: str = "", owner_id: str = "",
) -> list[dict]:
    """The owners this installation can see, as [{id, name}].

    GitHub's namespace is the owner, not a project — see the module docstring. The
    authenticated account is included alongside its organisations because a great many
    repositories live under a person rather than an org.
    """
    tok = await _token(token, tenant_id, project_id, owner_id)
    # DERIVED FROM THE REPOSITORIES THIS CREDENTIAL CAN ACTUALLY SEE, rather than from
    # the orgs it belongs to. Membership of an organisation is not permission over its
    # repositories, and offering an owner whose repos then come back empty sends people
    # looking for a repository that was never shared.
    owners: list[dict] = []
    seen: set[str] = set()
    for repo in await _visible_repos(tok):
        login = ((repo.get("owner") or {}).get("login") or "").strip()
        if login and login not in seen:
            seen.add(login)
            owners.append({"id": login, "name": login})
    return owners


async def list_repos(
    owner: str, token: str | None = None, tenant_id: str = "",
    *, project_id: str = "", owner_id: str = "",
) -> list[dict]:
    """Repos for an owner as [{id, name, default_branch, remote_url}] — ado_repos' shape."""
    tok = await _token(token, tenant_id, project_id, owner_id)
    repos: list[dict] = []
    for r in await _visible_repos(tok):
        if ((r.get("owner") or {}).get("login") or "") != owner:
            continue
        repos.append({
            "id": str(r.get("id") or ""),
            "name": r.get("name") or "",
            "default_branch": r.get("default_branch") or "",
            "remote_url": r.get("clone_url") or "",
        })
    return repos


async def list_branches(
    owner: str, repo: str, token: str | None = None, tenant_id: str = "",
    *, project_id: str = "", owner_id: str = "",
) -> list[dict]:
    """[{name, is_default}] — the same shape ado_repos returns, bare names."""
    tok = await _token(token, tenant_id, project_id, owner_id)
    default = ""
    try:
        meta = await _get(f"/repos/{owner}/{repo}", tok)
        default = meta.get("default_branch") or ""
    except Exception:  # noqa: BLE001 — a missing default only costs the flag
        pass
    return [
        {"name": b.get("name") or "", "is_default": (b.get("name") or "") == default}
        for b in await _paged(f"/repos/{owner}/{repo}/branches", tok)
    ]


async def resolve_clone_url(
    owner: str, repo: str, token: str | None = None, tenant_id: str = "",
    *, project_id: str = "", owner_id: str = "",
) -> str | None:
    """The HTTPS clone URL, or None when the repo is not visible to this installation."""
    tok = await _token(token, tenant_id, project_id, owner_id)
    try:
        meta = await _get(f"/repos/{owner}/{repo}", tok)
    except Exception as exc:  # noqa: BLE001
        logger.info("github resolve_clone_url: %s/%s -> %s", owner, repo, type(exc).__name__)
        return None
    return meta.get("clone_url") or None


async def list_pull_requests(
    owner: str, repo: str, token: str | None = None, tenant_id: str = "",
    *, project_id: str = "", owner_id: str = "",
) -> list[dict]:
    """Open PRs as [{id, title, source_branch, target_branch}] — ado_repos' shape.

    `id` is GitHub's `number`, which is what every GitHub URL and API path uses; the
    internal `id` would be useless to anybody reading it.
    """
    tok = await _token(token, tenant_id, project_id, owner_id)
    return [
        {
            "id": str(pr.get("number") or ""),
            "title": pr.get("title") or "",
            "source_branch": ((pr.get("head") or {}).get("ref") or ""),
            "target_branch": ((pr.get("base") or {}).get("ref") or ""),
        }
        for pr in await _paged(f"/repos/{owner}/{repo}/pulls", tok, {"state": "open"})
    ]


async def get_pull_request(
    owner: str, repo: str, number: str, token: str | None = None, tenant_id: str = "",
    *, project_id: str = "", owner_id: str = "",
) -> dict | None:
    """One PR, or None when it does not exist."""
    tok = await _token(token, tenant_id, project_id, owner_id)
    try:
        pr = await _get(f"/repos/{owner}/{repo}/pulls/{number}", tok)
    except Exception:  # noqa: BLE001
        return None
    return {
        "id": str(pr.get("number") or ""),
        "title": pr.get("title") or "",
        "source_branch": ((pr.get("head") or {}).get("ref") or ""),
        "target_branch": ((pr.get("base") or {}).get("ref") or ""),
    }
