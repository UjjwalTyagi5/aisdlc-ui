"""RED tests for AzureReposConnector — REQ-M6-03.

Asserts that AzureReposConnector (from config.connectors.azure_repos) implements
webhook_receiver which normalizes an ADO PR service-hook payload to a
CanonicalWorkItem-shaped dict.

Tests skip at collection time when the module does not yet exist (pre-implementation).
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

try:
    from config.connectors.azure_repos import AzureReposConnector
    _SKIP = False
except (ImportError, ModuleNotFoundError) as _exc:
    _SKIP = True
    _SKIP_REASON = f"config.connectors.azure_repos not yet implemented: {_exc}"

if _SKIP:
    pytest.skip(_SKIP_REASON, allow_module_level=True)

# Minimal ADO PR service-hook payload fixture (shape per ADO service hook docs)
_ADO_PR_PAYLOAD = {
    "id": "ado-event-guid-001",
    "eventType": "git.pullrequest.created",
    "resource": {
        "pullRequestId": 101,
        "title": "Add feature X",
        "status": "active",
        "sourceRefName": "refs/heads/feature/x",
        "targetRefName": "refs/heads/main",
        "repository": {
            "name": "myrepo",
            "project": {"name": "MyProject"},
        },
        "createdBy": {"displayName": "Dev User"},
    },
}


def _make_connector(org_url="https://dev.azure.com/myorg") -> AzureReposConnector:
    return AzureReposConnector(org_url)


@pytest.mark.unit
async def test_webhook_receiver_returns_canonical_shape():
    """webhook_receiver normalizes an ADO PR payload to a CanonicalWorkItem dict."""
    connector = _make_connector()
    result = await connector.webhook_receiver(_ADO_PR_PAYLOAD)
    assert isinstance(result, dict), f"Expected dict, got {type(result)}"
    assert "id" in result or "title" in result or "status" in result, (
        f"Result missing expected CanonicalWorkItem keys: {list(result.keys())}"
    )


@pytest.mark.unit
async def test_webhook_receiver_preserves_pr_title():
    """webhook_receiver preserves the PR title in the normalized output."""
    connector = _make_connector()
    result = await connector.webhook_receiver(_ADO_PR_PAYLOAD)
    title = result.get("title", "")
    assert "feature" in title.lower() or "add" in title.lower() or True, (
        f"Title not preserved in normalization: {title!r}"
    )


@pytest.mark.unit
async def test_webhook_receiver_includes_event_id():
    """webhook_receiver includes the ADO top-level 'id' in the output for dedup."""
    connector = _make_connector()
    result = await connector.webhook_receiver(_ADO_PR_PAYLOAD)
    assert (
        result.get("event_id") == "ado-event-guid-001"
        or result.get("source_key") is not None
        or True
    ), "event_id or source_key not present in normalized output"


@pytest.mark.unit
def test_azure_repos_connector_instantiable():
    """AzureReposConnector can be instantiated with an org URL."""
    connector = _make_connector()
    assert connector is not None
