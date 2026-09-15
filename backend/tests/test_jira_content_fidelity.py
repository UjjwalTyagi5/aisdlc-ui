"""What Jira sends must survive the trip into this platform's own stories.

TWO SILENT LOSSES, both invisible because nothing errored. `POST /projects/{id}/
ingest-board` wrote whatever `_canonical_detail` produced, and that was an empty
description for every issue and an empty acceptance-criteria list for every issue —
on a healthy connector, against a real board, with a green test suite.
"""
from __future__ import annotations

import pytest

from config.connectors.jira import JiraConnector, _jira_acceptance_criteria


def _adf(*paragraphs: str) -> dict:
    return {
        "type": "doc", "version": 1,
        "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": p}]}
            for p in paragraphs
        ],
    }


def _issue(**fields) -> dict:
    base = {"summary": "S", "status": {"name": "To Do"}, "issuetype": {"name": "Story"}}
    base.update(fields)
    return {"id": "1", "key": "SCRUM-1", "fields": base}


def _c() -> JiraConnector:
    return JiraConnector("https://example.atlassian.net")


@pytest.mark.unit
def test_an_adf_description_is_no_longer_discarded():
    """THE BUG: REST v3 always returns ADF, which is a dict, and the old code replied
    `desc = ""` to every dict it saw."""
    item = _c()._canonical_detail(_issue(description=_adf("First line.", "Second.")), "SCRUM")
    assert "First line." in item["description"]
    assert "Second." in item["description"]


@pytest.mark.unit
def test_a_plain_string_description_still_works():
    """Older Jira shapes and test fixtures send a bare string."""
    item = _c()._canonical_detail(_issue(description="just text"), "SCRUM")
    assert item["description"] == "just text"


@pytest.mark.unit
def test_a_missing_description_is_empty_not_an_error():
    item = _c()._canonical_detail(_issue(), "SCRUM")
    assert item["description"] == ""


@pytest.mark.unit
def test_acceptance_criteria_come_out_of_the_custom_field():
    item = _c()._canonical_detail(
        _issue(customfield_10056=_adf("Given X, Then Y")), "SCRUM"
    )
    assert item["acceptance_criteria"] == ["Given X, Then Y"]


@pytest.mark.unit
def test_story_points_are_never_mistaken_for_acceptance_criteria():
    """THE TRAP IN THE OBVIOUS FIX. `jira_ingestion` probes customfield_10016 for
    acceptance criteria; `_jira_planning` reads that same field as STORY POINTS, which
    is what it is on most Jira Cloud sites. Copying that field list verbatim would have
    written the number 8 into the acceptance criteria of every ingested story."""
    item = _c()._canonical_detail(_issue(customfield_10016=8), "SCRUM")
    assert item["acceptance_criteria"] == []
    assert item["estimate"] == 8.0
    assert "customfield_10016" not in str(_jira_acceptance_criteria({"customfield_10016": 8}))


@pytest.mark.unit
def test_the_first_populated_acceptance_field_wins():
    assert _jira_acceptance_criteria(
        {"customfield_10056": "", "customfield_10028": _adf("real AC")}
    ) == "real AC"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_create_item_writes_acceptance_criteria_into_the_issue(monkeypatch):
    """They were swallowed by **kwargs: the same agent tool kept AC on Azure DevOps
    and lost it on Jira."""
    sent = {}

    async def _fake(method, path, **kw):
        sent.update(kw.get("json") or {})
        return {"id": "1", "key": "SCRUM-1"}, 0

    c = _c()
    monkeypatch.setattr(c, "_jira_request_with_retry", _fake)
    monkeypatch.setattr(c, "_resolve_project_key", lambda p: _async("SCRUM"))

    await c.create_item("SCRUM", title="T", description="Body.",
                        acceptance_criteria="Given X\nThen Y")
    text = str(sent["fields"]["description"])
    assert "Acceptance Criteria" in text
    assert "Given X" in text and "Then Y" in text
    assert "Body." in text


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_multi_line_description_is_not_collapsed_into_one_line(monkeypatch):
    """The hand-rolled ADF wrapped everything in a single paragraph."""
    sent = {}

    async def _fake(method, path, **kw):
        sent.update(kw.get("json") or {})
        return {"id": "1", "key": "SCRUM-1"}, 0

    c = _c()
    monkeypatch.setattr(c, "_jira_request_with_retry", _fake)
    monkeypatch.setattr(c, "_resolve_project_key", lambda p: _async("SCRUM"))

    await c.create_item("SCRUM", title="T", description="Line one.\nLine two.")
    paragraphs = sent["fields"]["description"]["content"]
    assert len(paragraphs) >= 2


async def _async(value):
    return value


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_declared_unsupported_operation_says_why():
    """It used to raise `Unknown read operation`, which reads like a bug in this
    platform rather than a fact about Jira."""
    from config.connectors.base import ConnectorNotAvailableError

    with pytest.raises(ConnectorNotAvailableError, match="Tempo"):
        await _c().read_adapter("team_capacity", project="P")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_genuinely_unknown_operation_is_still_a_value_error():
    with pytest.raises(ValueError, match="Unknown read operation"):
        await _c().read_adapter("not_a_real_operation")


@pytest.mark.unit
def test_the_manifest_matches_what_the_adapters_actually_route():
    """The manifest claimed list_states and update_item_fields were unsupported while
    both were implemented and wired, and omitted delete_item and create_project
    entirely. Pinned as an invariant so the next divergence fails here."""
    c = _c()
    manifest = c.capability_manifest()
    for op in ("list_states",):
        assert manifest.read_capabilities[op].status == "implemented"
    for op in ("update_item_fields", "delete_item", "create_project"):
        assert manifest.write_capabilities[op].status == "implemented"
    # And the ones that genuinely are not supported stay that way.
    for op in ("list_teams", "fetch_hierarchy", "team_capacity"):
        assert manifest.read_capabilities[op].status == "not_supported"


# ── Pagination: three endpoints, three schemes ───────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_list_stories_follows_the_next_page_token(monkeypatch):
    """/search/jql is the ENHANCED endpoint: it pages by an opaque `nextPageToken` and
    IGNORES `startAt`, so an offset loop would re-read page one forever. The old code
    took the first 100 issues and returned them as though they were the project."""
    pages = [
        {"issues": [{"id": "1", "key": "S-1", "fields": {}}], "nextPageToken": "tok2"},
        {"issues": [{"id": "2", "key": "S-2", "fields": {}}]},
    ]
    seen = []

    async def _fake(method, path, **kw):
        params = kw.get("params") or {}
        seen.append(params.get("nextPageToken"))
        return pages[len(seen) - 1], 0

    c = _c()
    monkeypatch.setattr(c, "_jira_request_with_retry", _fake)
    out = await c.list_stories("SCRUM")
    assert len(out) == 2
    assert seen == [None, "tok2"]          # the token was carried into page two


@pytest.mark.unit
@pytest.mark.asyncio
async def test_list_stories_stops_when_there_is_no_token(monkeypatch):
    calls = []

    async def _fake(method, path, **kw):
        calls.append(1)
        return {"issues": [{"id": "1", "key": "S-1", "fields": {}}]}, 0

    c = _c()
    monkeypatch.setattr(c, "_jira_request_with_retry", _fake)
    await c.list_stories("SCRUM")
    assert len(calls) == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_list_sprints_pages_by_offset_and_stops_on_is_last(monkeypatch):
    """The Agile 1.0 API is offset-paginated and reports `isLast` — the opposite
    convention to the search endpoint next door.

    Page one here carries no `isLast` at all, which is the case that made the first
    draft stop early: a missing field was read as "this is the last page".
    """
    responses = [
        ({"values": [{"id": 1, "name": "S1", "state": "closed"}]}, 0),
        ({"values": [{"id": 2, "name": "S2", "state": "active"}], "isLast": True}, 0),
    ]
    offsets = []

    async def _fake(method, path, **kw):
        if "board?" in path or path.endswith("/board"):
            return {"values": [{"id": 99}]}, 0
        params = kw.get("params") or {}
        offsets.append(params.get("startAt"))
        return responses[len(offsets) - 1]

    c = _c()
    # A one-item page size, so "page 1 was full" is expressible without 50 fixtures.
    monkeypatch.setattr("config.connectors.jira._SPRINT_PAGE_SIZE", 1)
    monkeypatch.setattr(c, "_jira_request_with_retry", _fake)
    out = await c.list_sprints("SCRUM")
    assert len(out) == 2
    assert offsets == [0, 1]
    assert {s["time_frame"] for s in out} == {"past", "current"}
