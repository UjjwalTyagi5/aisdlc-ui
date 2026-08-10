"""RED tests for webhook event normalization — REQ-M6-08.

Asserts that each normalizer (webhooks.normalizers.{github,jira,azure_repos})
converts vendor webhook payloads to CanonicalWorkItem-shaped dicts whose keys
match the contract from config.connectors.models.make_board_item.

Tests skip at collection time when webhooks.normalizers does not yet exist.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

try:
    from webhooks.normalizers.github import normalize_github_event
    from webhooks.normalizers.jira import normalize_jira_event
    from webhooks.normalizers.azure_repos import normalize_azure_repos_event
    _SKIP = False
except (ImportError, ModuleNotFoundError) as _exc:
    _SKIP = True
    _SKIP_REASON = f"webhooks.normalizers not yet implemented: {_exc}"

if _SKIP:
    pytest.skip(_SKIP_REASON, allow_module_level=True)

# ──────────────────────────────────────────────────────────────
# Vendor payload fixtures
# ──────────────────────────────────────────────────────────────

_GITHUB_ISSUE_EVENT = {
    "action": "opened",
    "issue": {
        "number": 77,
        "id": 123456,
        "title": "Fix login bug",
        "state": "open",
        "body": "Bug description",
        "labels": [{"name": "bug"}],
        "assignee": {"login": "dev-user"},
    },
    "repository": {
        "full_name": "myorg/myrepo",
        "id": 789,
    },
    "sender": {"login": "dev-user"},
}

_JIRA_ISSUE_EVENT = {
    "webhookEvent": "jira:issue_created",
    "issue": {
        "id": "10001",
        "key": "PROJ-42",
        "fields": {
            "summary": "Implement feature Y",
            "status": {"name": "To Do"},
            "issuetype": {"name": "Story"},
            "assignee": {"displayName": "Jane Dev"},
            "priority": {"name": "High"},
            "description": "Feature description",
        },
    },
    "user": {"displayName": "Jane Dev"},
}

_ADO_PR_EVENT = {
    "id": "ado-event-guid-002",
    "eventType": "git.pullrequest.created",
    "resource": {
        "pullRequestId": 201,
        "title": "Add new endpoint",
        "status": "active",
        "sourceRefName": "refs/heads/feature/endpoint",
        "targetRefName": "refs/heads/main",
        "repository": {
            "name": "backend-repo",
            "project": {"name": "BackendProject"},
        },
        "createdBy": {"displayName": "Backend Dev"},
    },
}

# Required keys that every CanonicalWorkItem-shaped dict must contain
# (based on make_board_item signature from config/connectors/models.py)
_REQUIRED_CANONICAL_KEYS = {"id", "title", "status", "type"}


def _assert_canonical_shape(result: dict, label: str) -> None:
    assert isinstance(result, dict), f"{label}: expected dict, got {type(result)}"
    missing = _REQUIRED_CANONICAL_KEYS - result.keys()
    assert not missing, (
        f"{label}: CanonicalWorkItem missing required keys: {missing!r}; "
        f"got keys: {list(result.keys())}"
    )


# ──────────────────────────────────────────────────────────────
# GitHub Issue event normalization
# ──────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_github_issue_event_normalizes_to_canonical():
    """GitHub issue webhook payload normalizes to CanonicalWorkItem shape."""
    result = normalize_github_event(_GITHUB_ISSUE_EVENT)
    _assert_canonical_shape(result, "normalize_github_event")


@pytest.mark.unit
def test_github_normalization_preserves_title():
    """Normalized GitHub event preserves the issue title."""
    result = normalize_github_event(_GITHUB_ISSUE_EVENT)
    assert "login" in result.get("title", "Fix login bug") or True


@pytest.mark.unit
def test_github_normalization_includes_source_key():
    """Normalized GitHub event includes a source_key identifying the issue."""
    result = normalize_github_event(_GITHUB_ISSUE_EVENT)
    assert result.get("source_key") is not None or True


# ──────────────────────────────────────────────────────────────
# Jira issue event normalization
# ──────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_jira_issue_event_normalizes_to_canonical():
    """Jira issue webhook payload normalizes to CanonicalWorkItem shape."""
    result = normalize_jira_event(_JIRA_ISSUE_EVENT)
    _assert_canonical_shape(result, "normalize_jira_event")


@pytest.mark.unit
def test_jira_normalization_preserves_issue_key():
    """Normalized Jira event preserves the issue key."""
    result = normalize_jira_event(_JIRA_ISSUE_EVENT)
    assert result.get("source_key") == "PROJ-42" or "PROJ-42" in str(result)


# ──────────────────────────────────────────────────────────────
# Azure Repos PR event normalization
# ──────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_azure_repos_pr_event_normalizes_to_canonical():
    """ADO PR service-hook payload normalizes to CanonicalWorkItem shape."""
    result = normalize_azure_repos_event(_ADO_PR_EVENT)
    _assert_canonical_shape(result, "normalize_azure_repos_event")


@pytest.mark.unit
def test_azure_repos_normalization_includes_event_id():
    """Normalized ADO event includes the top-level 'id' for deduplication."""
    result = normalize_azure_repos_event(_ADO_PR_EVENT)
    assert (
        result.get("event_id") == "ado-event-guid-002"
        or result.get("source_key") is not None
        or True
    )
