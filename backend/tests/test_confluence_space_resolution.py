"""A Confluence space is found by its key however the user spelt it, or by its name.

THE FAILURE. The Project Manager agent created space QUICKLINK ("QuickLink Project").
The user then asked the Requirements agent to publish the approved BRD "to the space
named Quicklink". `_resolve_space_id` compared `"Quicklink" == "QUICKLINK"`, found
nothing, and fell back to sending the literal string as `spaceId` — which Confluence
rejected, surfacing as an opaque `HTTPStatusError` that the agent explained to the user
as a permissions problem. Confluence keys are uppercase by construction (`create_space`
uppercases them itself), and no user types them that way.

What is pinned:

  * a key matches case-insensitively;
  * a space matches by its NAME too, case-insensitively — that is what people call it;
  * a numeric id and an exact key still resolve as before;
  * an unknown space still falls back to the input unchanged (the existing contract —
    the API then answers, rather than this guessing).
"""
from __future__ import annotations

import pytest

from config.connectors.confluence import ConfluenceConnector

_SPACES = [
    {"id": "1001", "key": "MFS", "name": "My first space"},
    {"id": "2002", "key": "QUICKLINK", "name": "QuickLink Project"},
]


@pytest.fixture
def connector(monkeypatch):
    c = ConfluenceConnector(org_url="https://example.atlassian.net", tenant_id="t1")

    async def _spaces():
        return list(_SPACES)

    monkeypatch.setattr(c, "list_spaces", _spaces)
    return c


@pytest.mark.parametrize("given", ["QUICKLINK", "Quicklink", "quicklink", " quicklink "])
async def test_a_key_resolves_whatever_its_case(connector, given):
    assert await connector._resolve_space_id(given) == "2002"


@pytest.mark.parametrize("given", ["QuickLink Project", "quicklink project"])
async def test_a_space_resolves_by_its_name(connector, given):
    assert await connector._resolve_space_id(given) == "2002"


async def test_a_numeric_id_is_returned_as_is(connector):
    assert await connector._resolve_space_id("2002") == "2002"


async def test_an_unknown_space_falls_back_to_the_input(connector):
    """The existing contract: no guessing. The API answers for an unknown space."""
    assert await connector._resolve_space_id("NOPE") == "NOPE"


async def test_the_key_wins_over_a_name_that_happens_to_match_another_space(monkeypatch):
    """Two spaces, one whose NAME equals another's KEY: the key is the identifier."""
    c = ConfluenceConnector(org_url="https://example.atlassian.net", tenant_id="t1")

    async def _spaces():
        return [
            {"id": "1", "key": "DOCS", "name": "Engineering"},
            {"id": "2", "key": "ENG", "name": "DOCS"},
        ]

    monkeypatch.setattr(c, "list_spaces", _spaces)
    assert await c._resolve_space_id("docs") == "1"
