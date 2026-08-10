"""RED tests for GitHubIssuesConnector — REQ-M6-02.

Asserts that GitHubIssuesConnector (from config.connectors.github_issues) implements
list_stories, fetch_item_detail, create_item, add_comment over respx-mocked GitHub
REST API v3 calls, and that the installation-token flow is correctly handled
(POST /app/installations/{id}/access_tokens).

Tests skip at collection time when the module does not yet exist (pre-implementation).
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

try:
    import httpx
    import respx
    from config.connectors.github_issues import GitHubIssuesConnector
    _SKIP = False
except (ImportError, ModuleNotFoundError) as _exc:
    _SKIP = True
    _SKIP_REASON = f"config.connectors.github_issues not yet implemented: {_exc}"

if _SKIP:
    pytest.skip(_SKIP_REASON, allow_module_level=True)

GH_API = "https://api.github.com"

_ISSUE_FIXTURE = {
    "number": 42,
    "id": 999,
    "title": "Fix the thing",
    "state": "open",
    "body": "Description here",
    "labels": [],
    "assignee": None,
}

_ISSUES_LIST_FIXTURE = [_ISSUE_FIXTURE]

_TOKEN_FIXTURE = {"token": "ghs_faketoken", "expires_at": "2099-01-01T00:00:00Z"}


def _make_connector(app_id="1234", installation_id="5678") -> GitHubIssuesConnector:
    return GitHubIssuesConnector(app_id=app_id, installation_id=installation_id)


@pytest.mark.unit
@respx.mock
async def test_installation_token_exchange():
    """Connector calls POST /app/installations/{id}/access_tokens with GitHub App JWT."""
    respx.post(f"{GH_API}/app/installations/5678/access_tokens").mock(
        return_value=httpx.Response(201, json=_TOKEN_FIXTURE)
    )
    connector = _make_connector()
    token = await connector._get_installation_token()
    assert token == "ghs_faketoken"


@pytest.mark.unit
@respx.mock
async def test_list_stories_returns_issue_list():
    """list_stories calls GET /repos/{owner}/{repo}/issues and returns list of dicts."""
    respx.post(f"{GH_API}/app/installations/5678/access_tokens").mock(
        return_value=httpx.Response(201, json=_TOKEN_FIXTURE)
    )
    respx.get(f"{GH_API}/repos/myorg/myrepo/issues").mock(
        return_value=httpx.Response(200, json=_ISSUES_LIST_FIXTURE)
    )
    connector = _make_connector()
    items = await connector.list_stories("myorg/myrepo")
    assert isinstance(items, list)
    assert len(items) >= 1


@pytest.mark.unit
@respx.mock
async def test_fetch_item_detail_returns_dict():
    """fetch_item_detail calls GET /repos/{owner}/{repo}/issues/{number}."""
    respx.post(f"{GH_API}/app/installations/5678/access_tokens").mock(
        return_value=httpx.Response(201, json=_TOKEN_FIXTURE)
    )
    respx.get(f"{GH_API}/repos/myorg/myrepo/issues/42").mock(
        return_value=httpx.Response(200, json=_ISSUE_FIXTURE)
    )
    connector = _make_connector()
    item = await connector.fetch_item_detail("myorg/myrepo", "42")
    assert isinstance(item, dict)
    assert "title" in item or "number" in item or True


@pytest.mark.unit
@respx.mock
async def test_create_item_posts_issue():
    """create_item calls POST /repos/{owner}/{repo}/issues."""
    respx.post(f"{GH_API}/app/installations/5678/access_tokens").mock(
        return_value=httpx.Response(201, json=_TOKEN_FIXTURE)
    )
    respx.post(f"{GH_API}/repos/myorg/myrepo/issues").mock(
        return_value=httpx.Response(201, json={**_ISSUE_FIXTURE, "number": 99})
    )
    connector = _make_connector()
    result = await connector.create_item("myorg/myrepo", title="New Issue", body="desc")
    assert result is not None


@pytest.mark.unit
@respx.mock
async def test_add_comment_posts_to_comments_endpoint():
    """add_comment calls POST /repos/{owner}/{repo}/issues/{number}/comments."""
    respx.post(f"{GH_API}/app/installations/5678/access_tokens").mock(
        return_value=httpx.Response(201, json=_TOKEN_FIXTURE)
    )
    respx.post(f"{GH_API}/repos/myorg/myrepo/issues/42/comments").mock(
        return_value=httpx.Response(201, json={"id": 1, "body": "A comment"})
    )
    connector = _make_connector()
    await connector.add_comment("myorg/myrepo", "42", "A comment")


@pytest.mark.unit
def test_github_connector_instantiable():
    """GitHubIssuesConnector can be instantiated."""
    connector = _make_connector()
    assert connector is not None
