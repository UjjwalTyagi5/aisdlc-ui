"""Tests for ConfluenceConnector — mirrors test_m6_jira_connector.py's shape.

Asserts capability_manifest() declares BOTH read and write capabilities (the gap
this connector was added to close — see config/connectors/confluence.py), and that
each CRUD method hits the expected Confluence Cloud REST v2 (or v1, for the two
endpoints v2 doesn't cover) path via respx-mocked HTTP calls.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import httpx
import respx

from config.connectors.confluence import ConfluenceConnector

CONFLUENCE_BASE = "https://test.atlassian.net"
_SPACE_FIXTURE = {"results": [{"id": "111", "key": "ENG", "name": "Engineering"}]}
_PAGE_LIST_FIXTURE = {
    "results": [
        {
            "id": "222",
            "title": "Runbook",
            "status": "current",
            "spaceId": "111",
            "version": {"number": 3},
            "body": {"storage": {"value": "<p>hi</p>"}},
            "_links": {"webui": "/spaces/ENG/pages/222"},
        }
    ]
}
_PAGE_DETAIL_FIXTURE = _PAGE_LIST_FIXTURE["results"][0]


# The autouse fixture that blanked CONFLUENCE_URL/EMAIL/API_TOKEN is gone: auth_adapter
# no longer reads them, so a developer's .env can no longer redirect these requests off
# the respx mock.


def _make_connector() -> ConfluenceConnector:
    return ConfluenceConnector(CONFLUENCE_BASE)


@pytest.mark.unit
def test_confluence_connector_instantiable():
    connector = _make_connector()
    assert connector is not None
    assert connector.connector_name == "confluence"


@pytest.mark.unit
def test_capability_manifest_declares_read_and_write():
    """The manifest must declare BOTH halves — a connector with only one is exactly
    the gap `connector_capabilities.supported_level()` exists to catch and refuse."""
    manifest = _make_connector().capability_manifest()
    assert manifest.read_capabilities, "expected at least one read capability"
    assert manifest.write_capabilities, "expected at least one write capability"
    assert "list_pages" in manifest.read_capabilities
    assert "create_page" in manifest.write_capabilities
    assert "update_page" in manifest.write_capabilities


@pytest.mark.unit
@respx.mock
async def test_list_spaces_returns_picker_shape():
    respx.get(f"{CONFLUENCE_BASE}/wiki/api/v2/spaces").mock(
        return_value=httpx.Response(200, json=_SPACE_FIXTURE)
    )
    connector = _make_connector()
    spaces = await connector.list_spaces()
    assert spaces == [{"id": "111", "key": "ENG", "name": "Engineering"}]


@pytest.mark.unit
@respx.mock
async def test_list_pages_calls_v2_pages_endpoint():
    respx.get(f"{CONFLUENCE_BASE}/wiki/api/v2/pages").mock(
        return_value=httpx.Response(200, json=_PAGE_LIST_FIXTURE)
    )
    connector = _make_connector()
    pages = await connector.list_pages(space="111")
    assert len(pages) == 1
    assert pages[0]["title"] == "Runbook"


@pytest.mark.unit
@respx.mock
async def test_fetch_page_detail_returns_canonical_shape():
    respx.get(f"{CONFLUENCE_BASE}/wiki/api/v2/pages/222").mock(
        return_value=httpx.Response(200, json=_PAGE_DETAIL_FIXTURE)
    )
    connector = _make_connector()
    page = await connector.fetch_page_detail("222")
    assert page["id"] == "222"
    assert page["content"] == "<p>hi</p>"
    assert page["version"] == 3


@pytest.mark.unit
@respx.mock
async def test_create_page_posts_to_pages_endpoint():
    respx.get(f"{CONFLUENCE_BASE}/wiki/api/v2/spaces").mock(
        return_value=httpx.Response(200, json=_SPACE_FIXTURE)
    )
    respx.post(f"{CONFLUENCE_BASE}/wiki/api/v2/pages").mock(
        return_value=httpx.Response(200, json=_PAGE_DETAIL_FIXTURE)
    )
    connector = _make_connector()
    result = await connector.create_page("ENG", title="Runbook", content="<p>hi</p>")
    assert result["id"] == "222"


@pytest.mark.unit
@respx.mock
async def test_update_page_fetches_current_version_when_not_supplied():
    """Confluence rejects a stale/guessed version with 409, so when the caller
    doesn't pass one, update_page must fetch the current page first to derive it."""
    respx.get(f"{CONFLUENCE_BASE}/wiki/api/v2/pages/222").mock(
        return_value=httpx.Response(200, json=_PAGE_DETAIL_FIXTURE)
    )
    put_route = respx.put(f"{CONFLUENCE_BASE}/wiki/api/v2/pages/222").mock(
        return_value=httpx.Response(200, json=_PAGE_DETAIL_FIXTURE)
    )
    connector = _make_connector()
    await connector.update_page("222", title="Runbook v2")
    import json as _json
    sent_body = _json.loads(put_route.calls.last.request.content)
    assert sent_body["version"]["number"] == 4  # 3 (current) + 1


@pytest.mark.unit
@respx.mock
async def test_delete_page_calls_delete():
    respx.delete(f"{CONFLUENCE_BASE}/wiki/api/v2/pages/222").mock(
        return_value=httpx.Response(204)
    )
    connector = _make_connector()
    result = await connector.delete_page("222")
    assert result == {"page_id": "222", "deleted": True}


@pytest.mark.unit
@respx.mock
async def test_add_comment_posts_to_v1_content_endpoint():
    respx.post(f"{CONFLUENCE_BASE}/wiki/rest/api/content").mock(
        return_value=httpx.Response(200, json={"id": "999"})
    )
    connector = _make_connector()
    result = await connector.add_comment("222", "hello")
    assert result == {"id": "999", "page_id": "222"}


@pytest.mark.unit
@respx.mock
async def test_read_adapter_dispatches_list_pages():
    respx.get(f"{CONFLUENCE_BASE}/wiki/api/v2/pages").mock(
        return_value=httpx.Response(200, json=_PAGE_LIST_FIXTURE)
    )
    connector = _make_connector()
    result = await connector.read_adapter("list_pages", space="111")
    assert len(result) == 1


@pytest.mark.unit
@respx.mock
async def test_write_adapter_dispatches_create_page():
    respx.get(f"{CONFLUENCE_BASE}/wiki/api/v2/spaces").mock(
        return_value=httpx.Response(200, json=_SPACE_FIXTURE)
    )
    respx.post(f"{CONFLUENCE_BASE}/wiki/api/v2/pages").mock(
        return_value=httpx.Response(200, json=_PAGE_DETAIL_FIXTURE)
    )
    connector = _make_connector()
    result = await connector.write_adapter("create_page", space="ENG", title="Runbook")
    assert result["id"] == "222"


@pytest.mark.unit
async def test_read_adapter_rejects_unknown_operation():
    connector = _make_connector()
    with pytest.raises(ValueError):
        await connector.read_adapter("not_a_real_operation")


@pytest.mark.unit
async def test_write_adapter_rejects_unknown_operation():
    connector = _make_connector()
    with pytest.raises(ValueError):
        await connector.write_adapter("not_a_real_operation")


# ── Space creation and attachments ───────────────────────────────────────────
#
# Both were missing entirely, which is why a PM-agent session asked to "create a space
# and upload this document" got a truthful refusal. They are v1 endpoints: v2 exposes
# spaces read-only and has no attachment upload.


@pytest.mark.unit
@respx.mock
async def test_create_space_posts_to_the_v1_space_endpoint():
    route = respx.post(f"{CONFLUENCE_BASE}/wiki/rest/api/space").mock(
        return_value=httpx.Response(
            200,
            json={"id": 9, "key": "QUICKLINK", "name": "QuickLink",
                  "_links": {"webui": "/spaces/QUICKLINK"}},
        )
    )
    connector = _make_connector()
    result = await connector.create_space("QUICKLINK", name="QuickLink")
    assert result["key"] == "QUICKLINK"
    assert route.called
    import json as _json
    assert _json.loads(route.calls.last.request.content)["key"] == "QUICKLINK"


@pytest.mark.unit
@respx.mock
async def test_create_space_normalises_the_key():
    """Confluence will not accept a key with punctuation, and will not let it be
    changed afterwards — so it is normalised here rather than failing server-side
    with a message about a regex."""
    route = respx.post(f"{CONFLUENCE_BASE}/wiki/rest/api/space").mock(
        return_value=httpx.Response(200, json={"key": "QUICKLINK", "name": "x"})
    )
    connector = _make_connector()
    await connector.create_space("quick-link 01", name="x")
    import json as _json
    assert _json.loads(route.calls.last.request.content)["key"] == "QUICKLINK01"


@pytest.mark.unit
async def test_create_space_refuses_an_empty_key():
    connector = _make_connector()
    with pytest.raises(ValueError, match="space key is required"):
        await connector.create_space("   ")


@pytest.mark.unit
@respx.mock
async def test_upload_attachment_sends_multipart_with_the_xsrf_header():
    """Without `X-Atlassian-Token: nocheck` Confluence rejects the upload as a
    suspected XSRF attempt, with a 403 whose body talks about tokens, not files."""
    route = respx.post(
        f"{CONFLUENCE_BASE}/wiki/rest/api/content/222/child/attachment"
    ).mock(
        return_value=httpx.Response(
            200,
            json={"results": [{"id": "att1", "title": "BRD.docx",
                               "_links": {"download": "/download/BRD.docx"}}]},
        )
    )
    connector = _make_connector()
    result = await connector.upload_attachment(
        "222", "BRD.docx", b"bytes", content_type="application/vnd.openxmlformats"
    )
    assert result["id"] == "att1"
    assert result["page_id"] == "222"
    request = route.calls.last.request
    assert request.headers["X-Atlassian-Token"] == "nocheck"
    assert request.headers["content-type"].startswith("multipart/form-data")
    assert b"BRD.docx" in request.content


@pytest.mark.unit
async def test_upload_attachment_refuses_an_empty_file():
    """A zero-byte attachment uploads happily and is useless on the page."""
    connector = _make_connector()
    with pytest.raises(ValueError, match="empty"):
        await connector.upload_attachment("222", "BRD.docx", b"")


@pytest.mark.unit
async def test_upload_attachment_requires_a_page():
    connector = _make_connector()
    with pytest.raises(ValueError, match="page id is required"):
        await connector.upload_attachment("", "BRD.docx", b"bytes")


@pytest.mark.unit
def test_the_new_write_operations_are_dispatchable_and_declared():
    """A capability that the adapter cannot route is unreachable from an agent."""
    connector = _make_connector()
    manifest = connector.capability_manifest()
    for op in ("create_space", "upload_attachment"):
        assert manifest.write_capabilities[op].status == "implemented"
