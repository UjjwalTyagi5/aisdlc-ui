"""`update_page` always sends the page's title, because Confluence v2 requires it.

THE FAILURE. The publisher rewrote a page body after attaching the file and passed only
the new body. `update_page` built a payload with `id`, `status`, `version` and `body`,
and Confluence answered `400 INVALID_REQUEST_BODY — "Only a Page with a status of
DRAFT can have an empty title."` The page kept its old body and the reader saw no file.

`update_page` already fetches the current page to derive the next version number when
none is given; the title comes from the same fetch. When a version IS given and no
title, the fetch happens for the title alone — one read is cheaper than a 400.
"""
from __future__ import annotations

import pytest

from config.connectors.confluence import ConfluenceConnector


@pytest.fixture
def connector(monkeypatch):
    c = ConfluenceConnector(org_url="https://example.atlassian.net", tenant_id="t1")
    sent: list[dict] = []

    async def _detail(page_id):
        return {"id": page_id, "title": "QuickLink_BRD_new", "version": 3, "status": "current"}

    async def _request(method, path, tenant_id="", v1=False, **kwargs):
        sent.append({"method": method, "path": path, "json": kwargs.get("json")})
        return {"id": "1114113", "title": kwargs["json"].get("title"), "version": {"number": 4}}, 0

    monkeypatch.setattr(c, "fetch_page_detail", _detail)
    monkeypatch.setattr(c, "_confluence_request_with_retry", _request)
    c._sent = sent  # type: ignore[attr-defined]
    return c


async def test_a_body_only_update_carries_the_current_title(connector):
    await connector.update_page("1114113", content="<p>new body</p>")

    payload = connector._sent[0]["json"]
    assert payload["title"] == "QuickLink_BRD_new"
    assert payload["version"] == {"number": 4}
    assert payload["body"]["value"] == "<p>new body</p>"


async def test_an_explicit_title_is_kept(connector):
    await connector.update_page("1114113", title="Renamed", content="<p>x</p>")
    assert connector._sent[0]["json"]["title"] == "Renamed"


async def test_a_given_version_with_no_title_still_sends_the_title(connector):
    await connector.update_page("1114113", content="<p>x</p>", version=9)
    payload = connector._sent[0]["json"]
    assert payload["title"] == "QuickLink_BRD_new"
    assert payload["version"] == {"number": 9}
