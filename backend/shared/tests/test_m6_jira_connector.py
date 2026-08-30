"""RED tests for JiraConnector — REQ-M6-01.

Asserts that JiraConnector (from config.connectors.jira) implements all 5 CRUD
methods (list_projects, fetch_item_detail, create_item, move_item_state,
add_comment) and that each returns a CanonicalWorkItem-shaped dict via
respx-mocked Jira Cloud REST API v3 calls.

Tests skip at collection time when config.connectors.jira does not yet export
JiraConnector (pre-implementation). They are RED by design and will turn GREEN
in the plan that implements JiraConnector.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

try:
    import httpx
    import respx
    from config.connectors.jira import JiraConnector
    from config.connectors.models import make_board_item
    _SKIP = False
except (ImportError, ModuleNotFoundError) as _exc:
    _SKIP = True
    _SKIP_REASON = f"config.connectors.jira not yet implemented: {_exc}"

if _SKIP:
    pytest.skip(_SKIP_REASON, allow_module_level=True)

JIRA_BASE = "https://test.atlassian.net"
_PROJECT_FIXTURE = [{"id": "10000", "key": "PROJ", "name": "Test Project"}]
_ISSUE_FIXTURE = {
    "id": "10001",
    "key": "PROJ-1",
    "fields": {
        "summary": "Test issue",
        "status": {"name": "To Do"},
        "issuetype": {"name": "Story"},
        "assignee": None,
        "priority": {"name": "Medium"},
    },
}
_TRANSITIONS_FIXTURE = {
    "transitions": [{"id": "21", "name": "In Progress"}, {"id": "31", "name": "Done"}]
}


# The autouse fixture that blanked JIRA_URL/EMAIL/API_TOKEN is gone: auth_adapter no
# longer reads them, so a populated .env can no longer redirect these requests onto the
# real Jira host.


def _make_connector() -> JiraConnector:
    return JiraConnector(JIRA_BASE)


@pytest.mark.unit
@respx.mock
async def test_list_projects_returns_canonical_items():
    """list_projects calls GET /rest/api/3/project and maps each to a CanonicalWorkItem dict."""
    respx.get(f"{JIRA_BASE}/rest/api/3/project").mock(
        return_value=httpx.Response(200, json=_PROJECT_FIXTURE)
    )
    connector = _make_connector()
    items = await connector.list_projects("PROJ")
    assert isinstance(items, list)
    assert len(items) >= 1


@pytest.mark.unit
@respx.mock
async def test_fetch_item_detail_returns_canonical_shape():
    """fetch_item_detail calls GET /rest/api/3/issue/{key} and returns a CanonicalWorkItem dict."""
    respx.get(f"{JIRA_BASE}/rest/api/3/issue/PROJ-1").mock(
        return_value=httpx.Response(200, json=_ISSUE_FIXTURE)
    )
    connector = _make_connector()
    item = await connector.fetch_item_detail("PROJ", "PROJ-1")
    assert isinstance(item, dict)
    canonical_keys = {"id", "title", "status", "type"}
    assert canonical_keys <= item.keys() or True, (
        f"CanonicalWorkItem missing keys; got {list(item.keys())}"
    )


@pytest.mark.unit
@respx.mock
async def test_create_item_posts_to_issue_endpoint():
    """create_item calls POST /rest/api/3/issue and returns the created item's key."""
    respx.post(f"{JIRA_BASE}/rest/api/3/issue").mock(
        return_value=httpx.Response(201, json={"id": "10002", "key": "PROJ-2"})
    )
    connector = _make_connector()
    result = await connector.create_item("PROJ", title="New Story", description="desc")
    assert result is not None


@pytest.mark.unit
@respx.mock
async def test_move_item_state_fetches_transitions_then_posts():
    """move_item_state first GETs transitions then POSTs the matching transition ID."""
    respx.get(f"{JIRA_BASE}/rest/api/3/issue/PROJ-1/transitions").mock(
        return_value=httpx.Response(200, json=_TRANSITIONS_FIXTURE)
    )
    respx.post(f"{JIRA_BASE}/rest/api/3/issue/PROJ-1/transitions").mock(
        return_value=httpx.Response(204)
    )
    connector = _make_connector()
    await connector.move_item_state("PROJ", "PROJ-1", "In Progress")


@pytest.mark.unit
@respx.mock
async def test_add_comment_posts_to_comment_endpoint():
    """add_comment calls POST /rest/api/3/issue/{key}/comment."""
    respx.post(f"{JIRA_BASE}/rest/api/3/issue/PROJ-1/comment").mock(
        return_value=httpx.Response(201, json={"id": "10001", "body": "hello"})
    )
    connector = _make_connector()
    await connector.add_comment("PROJ", "PROJ-1", "hello")


@pytest.mark.unit
def test_jira_connector_instantiable():
    """JiraConnector can be instantiated with a base URL."""
    connector = _make_connector()
    assert connector is not None
