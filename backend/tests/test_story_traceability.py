"""Traceability shows the links that exist, and the work item actually links.

FOUR ROWS, ONE OF WHICH CARRIED DATA, AND EVEN THAT ONE DID NOT LINK.

  Work item     — the only populated row. `jiraIssueKey` is set from the board's source
                  key, but the panel built its href from a `jiraBaseUrl` prop that NO
                  caller passed, so the key rendered as inert text. Nothing in the
                  payload said which Jira site or ADO organisation the key belonged to.
  Design        — `designArtifactId`. Its only writer in the whole repository is
                  scripts/seed_e2e_fixtures.py.
  Pull request  — `prUrl`. Same: the fixture script and nothing else.
  Tests         — `value: undefined`, hardcoded, with a comment reserving it for a
                  future chunk. It could not display anything under any circumstances.

So three rows read "unlinked" on every story forever, which advertises a feature rather
than reporting a state — the same shape as the placeholder comments removed earlier.

The URL is now resolved at INGEST, which is the only place holding both the connector
and the item.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.routers._schemas import story_artifacts_from_run  # noqa: E402
from shared.routers.projects import _board_item_url  # noqa: E402


class _Connector:
    def __init__(self, kind, org_url):
        self.connector_name = kind
        self._org_url = org_url


def _run(stories):
    return SimpleNamespace(
        id="11111111-1111-1111-1111-111111111111",
        created_at=datetime(2026, 9, 2, tzinfo=timezone.utc),
        requirements_payload={"stories": stories},
    )


def _story(**kw):
    return {
        "source_key": kw.get("source_key", "SCRUM-15"),
        "title": "Documentation & Knowledge Transfer",
        "description": "",
        "acceptance_criteria": [],
        "work_item_type": "Epic",
        **({"url": kw["url"]} if "url" in kw else {}),
    }


# -- building the URL ----------------------------------------------------------


@pytest.mark.unit
def test_a_jira_item_links_by_issue_key():
    url = _board_item_url(
        _Connector("jira", "https://acme.atlassian.net/"), "My Software Team",
        "SCRUM-15", {"id": "10079"},
    )
    assert url == "https://acme.atlassian.net/browse/SCRUM-15"


@pytest.mark.unit
def test_an_azure_devops_item_links_by_numeric_id_under_its_project():
    """ADO addresses work items by id, not by the source key — one template cannot
    serve both providers."""
    url = _board_item_url(
        _Connector("azure_devops", "https://dev.azure.com/acme"), "Test Project",
        "42", {"id": "42"},
    )
    assert url == "https://dev.azure.com/acme/Test%20Project/_workitems/edit/42"


@pytest.mark.unit
def test_a_board_name_with_spaces_is_encoded():
    url = _board_item_url(
        _Connector("azure_devops", "https://dev.azure.com/acme"), "My Team/Project",
        "7", {"id": "7"},
    )
    assert " " not in url and "/_workitems/" in url
    assert "My%20Team%2FProject" in url


@pytest.mark.unit
@pytest.mark.parametrize(
    ("kind", "org", "key", "detail"),
    [
        ("github", "https://x", "K-1", {"id": "1"}),      # unknown provider
        ("jira", "", "SCRUM-1", {"id": "1"}),             # no org url
        ("jira", "https://x", "", {"id": "1"}),           # no source key
        ("azure_devops", "https://x", "K", {}),           # ADO with no numeric id
    ],
)
def test_an_unresolvable_url_is_empty_not_a_guess(kind, org, key, detail):
    """A wrong link is worse than none: it looks live and 404s on click."""
    assert _board_item_url(_Connector(kind, org), "b", key, detail) == ""


@pytest.mark.unit
def test_it_never_raises_on_a_broken_connector():
    """A missing link is cosmetic; ingestion has already done the expensive part by the
    time this runs and must not fail for it."""
    assert _board_item_url(None, "b", "K-1", {}) == ""
    assert _board_item_url(object(), "b", "K-1", {}) == ""


# -- carrying it into traceability --------------------------------------------


@pytest.mark.unit
def test_a_freshly_ingested_story_carries_its_board_url():
    story = story_artifacts_from_run(
        _run([_story(url="https://acme.atlassian.net/browse/SCRUM-15")]), "p"
    )[0]
    trace = story.body["traceability"]
    assert trace["jiraIssueKey"] == "SCRUM-15"
    assert trace["boardUrl"] == "https://acme.atlassian.net/browse/SCRUM-15"


@pytest.mark.unit
def test_a_story_ingested_before_this_still_shows_its_key():
    """Payloads written earlier have no url. The key must still render — it just does
    not link — rather than the row vanishing or the key being dropped."""
    trace = story_artifacts_from_run(_run([_story()]), "p")[0].body["traceability"]
    assert trace == {"jiraIssueKey": "SCRUM-15"}


@pytest.mark.unit
def test_an_empty_url_is_omitted_rather_than_sent_as_a_blank_link():
    """The Zod schema requires a URL; "" would fail validation and blank the story."""
    trace = story_artifacts_from_run(_run([_story(url="")]), "p")[0].body["traceability"]
    assert "boardUrl" not in trace


@pytest.mark.unit
def test_an_item_with_no_source_key_has_no_traceability_at_all():
    trace = story_artifacts_from_run(_run([_story(source_key="")]), "p")[0].body["traceability"]
    assert trace == {}
