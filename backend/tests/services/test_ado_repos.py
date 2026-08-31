"""Tests for shared/services/ado_repos.py — ADO projects/repos/branches helper.

Uses monkeypatching to avoid real HTTP calls. Asserts the parsing logic against
the real ADO REST API JSON shapes (value[] envelope with camelCase fields).
"""
from __future__ import annotations

import pytest
import pytest_asyncio

# ---------------------------------------------------------------------------
# Fixtures: canonical ADO REST JSON shapes
# ---------------------------------------------------------------------------

_PROJECTS_RESPONSE = {
    "count": 2,
    "value": [
        {"id": "proj-111", "name": "AlphaProject"},
        {"id": "proj-222", "name": "BetaProject"},
    ],
}

_REPOS_RESPONSE = {
    "count": 2,
    "value": [
        {
            "id": "repo-aaa",
            "name": "alpha-service",
            "defaultBranch": "refs/heads/main",
            "remoteUrl": "https://dev.azure.com/org/AlphaProject/_git/alpha-service",
        },
        {
            "id": "repo-bbb",
            "name": "beta-service",
            "defaultBranch": "refs/heads/develop",
            "remoteUrl": "https://dev.azure.com/org/AlphaProject/_git/beta-service",
        },
    ],
}

_REFS_RESPONSE = {
    "count": 3,
    "value": [
        {"name": "refs/heads/main", "objectId": "abc"},
        {"name": "refs/heads/feature/x", "objectId": "def"},
        {"name": "refs/heads/develop", "objectId": "ghi"},
    ],
}


# ---------------------------------------------------------------------------
# Helper: build a fake httpx response
# ---------------------------------------------------------------------------

class _FakeResponse:
    def __init__(self, payload: dict):
        self._payload = payload
        self.status_code = 200

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _FakeClient:
    """Minimal async context-manager that returns a canned response for GET."""

    def __init__(self, payload: dict):
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        pass

    async def get(self, url, **kwargs):
        return _FakeResponse(self._payload)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

pytestmark = pytest.mark.asyncio


async def test_list_projects_returns_id_and_name(monkeypatch):
    """list_projects() parses ADO value[] into [{id, name}]."""
    import httpx
    from shared.services import ado_repos

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: _FakeClient(_PROJECTS_RESPONSE))
    async def _fake_resolve_auth(tenant_id="", **kwargs):
        return "https://dev.azure.com/testorg", "secret-pat"

    monkeypatch.setattr(ado_repos, "resolve_auth", _fake_resolve_auth)

    result = await ado_repos.list_projects()

    assert result == [
        {"id": "proj-111", "name": "AlphaProject"},
        {"id": "proj-222", "name": "BetaProject"},
    ]


async def test_list_repos_parses_camel_fields(monkeypatch):
    """list_repos() maps camelCase ADO fields to snake_case output."""
    import httpx
    from shared.services import ado_repos

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: _FakeClient(_REPOS_RESPONSE))
    async def _fake_resolve_auth(tenant_id="", **kwargs):
        return "https://dev.azure.com/testorg", "secret-pat"

    monkeypatch.setattr(ado_repos, "resolve_auth", _fake_resolve_auth)

    result = await ado_repos.list_repos("AlphaProject")

    assert result == [
        {
            "id": "repo-aaa",
            "name": "alpha-service",
            "default_branch": "refs/heads/main",
            "remote_url": "https://dev.azure.com/org/AlphaProject/_git/alpha-service",
        },
        {
            "id": "repo-bbb",
            "name": "beta-service",
            "default_branch": "refs/heads/develop",
            "remote_url": "https://dev.azure.com/org/AlphaProject/_git/beta-service",
        },
    ]


async def test_list_branches_strips_refs_prefix_and_marks_default(monkeypatch):
    """list_branches() strips 'refs/heads/' and sets is_default based on the repo's defaultBranch."""
    import httpx
    from shared.services import ado_repos

    # list_branches needs list_repos to resolve name→id; fake both calls.
    call_count = 0

    class _SequentialClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        async def get(self, url, **kwargs):
            nonlocal call_count
            call_count += 1
            if "repositories?" in url:
                return _FakeResponse(_REPOS_RESPONSE)
            return _FakeResponse(_REFS_RESPONSE)

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: _SequentialClient())
    async def _fake_resolve_auth(tenant_id="", **kwargs):
        return "https://dev.azure.com/testorg", "secret-pat"

    monkeypatch.setattr(ado_repos, "resolve_auth", _fake_resolve_auth)

    result = await ado_repos.list_branches("AlphaProject", "alpha-service")

    # refs/heads/ stripped; main is_default because defaultBranch = refs/heads/main
    assert result == [
        {"name": "main", "is_default": True},
        {"name": "feature/x", "is_default": False},
        {"name": "develop", "is_default": False},
    ]


async def test_list_branches_accepts_repo_id_directly(monkeypatch):
    """list_branches() skips the repos-fetch step when given a real UUID repo id.

    Real ADO repo ids are UUIDs (8-4-4-4-12 hex). When a UUID is supplied the
    helper goes straight to the refs endpoint — no extra round-trip for resolution.
    is_default is False for all branches because there is no repo object to
    compare the defaultBranch against.
    """
    import httpx
    from shared.services import ado_repos

    repos_called = False
    _REAL_UUID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"

    class _RepoIdClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        async def get(self, url, **kwargs):
            nonlocal repos_called
            if "repositories?" in url:
                repos_called = True
            return _FakeResponse(_REFS_RESPONSE)

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: _RepoIdClient())
    async def _fake_resolve_auth(tenant_id="", **kwargs):
        return "https://dev.azure.com/testorg", "secret-pat"

    monkeypatch.setattr(ado_repos, "resolve_auth", _fake_resolve_auth)

    result = await ado_repos.list_branches("AlphaProject", _REAL_UUID)

    assert not repos_called, "Should not fetch repos when a UUID id is given directly"
    assert all("name" in b and "is_default" in b for b in result)
    assert all(b["is_default"] is False for b in result)


async def test_inject_pat_uses_nonempty_username():
    """_inject_pat() must emit a NON-EMPTY username. Regression for the 84-char
    Azure DevOps PAT that rejects Basic auth with an empty username — the classic
    `https://:{pat}@` form failed clone with 'Authentication failed' (while REST,
    which sends base64(":pat"), still worked). Any non-empty username fixes it."""
    import urllib.parse
    from shared.services import ado_repos

    remote = "https://srk02804@dev.azure.com/srk02804/Company/_git/Company"
    pat = "8jzAlphaNumericTokenNoSpecials9E"

    url = ado_repos._inject_pat(remote, pat)
    parsed = urllib.parse.urlparse(url)

    # The actual bug: username must not be empty
    assert parsed.username, "PAT clone URL must carry a non-empty username"
    # PAT is the password; host/path stay intact
    assert urllib.parse.unquote(parsed.password) == pat
    assert parsed.hostname == "dev.azure.com"
    assert parsed.path == "/srk02804/Company/_git/Company"


async def test_inject_pat_percent_encodes_special_chars():
    """_inject_pat() percent-encodes a PAT with URL-reserved chars so the netloc
    stays intact (defensive — a token with '/', '+', '=' or ':' would otherwise
    mangle the host)."""
    import urllib.parse
    from shared.services import ado_repos

    remote = "https://srk02804@dev.azure.com/srk02804/Company/_git/Company"
    pat = "ab/cd+ef=gh:ij"

    url = ado_repos._inject_pat(remote, pat)
    parsed = urllib.parse.urlparse(url)

    # Host must survive intact (a raw '/' in the PAT would otherwise bleed into it)
    assert parsed.hostname == "dev.azure.com"
    assert parsed.path == "/srk02804/Company/_git/Company"
    # Git decodes the userinfo before use: it must round-trip back to the exact PAT
    assert urllib.parse.unquote(parsed.password) == pat
    # The raw (unencoded) PAT must NOT appear literally in the URL
    assert pat not in url


async def test_scrub_redacts_raw_and_encoded_pat():
    """_scrub() removes both the raw and percent-encoded PAT so neither leaks into a
    RuntimeError surfaced to the UI (the clone URL carries the encoded form)."""
    import urllib.parse
    from shared.services import ado_repos

    pat = "ab/cd+ef"
    text = f"fatal: could not read from https://:{urllib.parse.quote(pat, safe='')}@host and raw {pat}"
    scrubbed = ado_repos._scrub(text, pat)

    assert pat not in scrubbed
    assert urllib.parse.quote(pat, safe="") not in scrubbed
    assert "***" in scrubbed


async def test_resolve_auth_forwards_project_id_and_owner_id_to_the_connector_factory(monkeypatch):
    """resolve_auth(tenant_id, project_id=, owner_id=) must forward both through to
    get_connector_for_session so a project-scoped personal credential
    (project_integration_credentials) can be found — not just the tenant-wide one.

    Regression for a real bug found during live verification: a project admin
    saved an Azure DevOps PAT for their own project via the Integrations page
    (which writes project_integration_credentials + the matching app_secrets
    row), but the "Pull repo" dialog still failed with "Couldn't reach Azure
    DevOps" — resolve_auth only ever passed tenant_id to
    get_connector_for_session, so the project-scoped credential was never
    looked up and it fell through to the (nonexistent) tenant-wide one.
    """
    from config import connector_factory
    from shared.services import ado_repos

    captured: dict = {}

    class _FakeConn:
        async def auth_adapter(self, tenant_id):
            return {"org_url": "https://dev.azure.com/srk02804", "pat": "resolved-pat"}

    async def _fake_get_connector_for_session(kind, tenant_id="", **kwargs):
        captured.update(kwargs)
        return _FakeConn()

    monkeypatch.setattr(
        connector_factory, "get_connector_for_session", _fake_get_connector_for_session
    )

    org, pat = await ado_repos.resolve_auth(
        "tenant-1", project_id="project-1", owner_id="owner-1"
    )

    assert org == "https://dev.azure.com/srk02804"
    assert pat == "resolved-pat"
    assert captured.get("project_id") == "project-1"
    assert captured.get("owner_id") == "owner-1"


async def test_list_branches_uses_override_pat_not_connector_default(monkeypatch):
    """list_branches() uses the caller-supplied pat, not the connector-resolved one.

    This proves that per-session PAT overrides (s.pat) injected by git_tools Path B
    are forwarded through ado_repos helpers and not silently dropped.
    """
    import httpx
    from shared.services import ado_repos

    captured_headers: list[dict] = []

    class _CapturingClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        async def get(self, url, **kwargs):
            captured_headers.append(kwargs.get("headers", {}))
            if "repositories?" in url:
                return _FakeResponse(_REPOS_RESPONSE)
            return _FakeResponse(_REFS_RESPONSE)

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: _CapturingClient())
    async def _fake_resolve_auth(tenant_id="", **kwargs):
        return "https://dev.azure.com/testorg", "connector-resolved-pat"

    monkeypatch.setattr(ado_repos, "resolve_auth", _fake_resolve_auth)

    import base64
    session_pat = "session-override-pat"
    expected_token = base64.b64encode(f":{session_pat}".encode()).decode()

    await ado_repos.list_branches("AlphaProject", "alpha-service", pat=session_pat)

    # Every HTTP call must carry the session PAT, not the module-level one
    assert len(captured_headers) >= 1, "Expected at least one HTTP call"
    for headers in captured_headers:
        auth = headers.get("Authorization", "")
        assert f"Basic {expected_token}" == auth, (
            f"Expected session PAT in Authorization header, got: {auth!r}"
        )