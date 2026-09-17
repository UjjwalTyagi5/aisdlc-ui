"""Publishing to Confluence is repeatable, and an attached file is visible on its page.

THE LIVE FAILURES, both on 15 Sep 2026 in the Design agent:

1. `publish_approved_to_confluence(space="QUICKLINK", filename="architecture.docx")`
   ran twice — the first reply was lost when the socket closed — and the second got
   `400 A page with this title already exists in the space`, which the agent relayed
   as "check the space key / your permissions". Confluence keeps page titles unique
   within a space; a page already carrying the document's title IS that document's
   page. A second publish now updates it and versions its attachment.

2. Asked for a page called "Design Documents" with the file on it, the agent used
   `create_confluence_page` + `attach_file_to_confluence_page`, and the page showed
   one sentence and no file: attachments live under the page's ⋯ menu unless the
   body links them. The attach tool now appends the file card to the body, the same
   card `publish_approved_to_confluence` writes.
"""
from __future__ import annotations

import httpx
import pytest

from shared.tools import confluence_artifacts as ca

_DOC = {
    "id": "eee5170d-ab90-4d50-998a-cd6def280e2e",
    "name": "architecture.docx",
    "blob_path": "t/u/p/design/r/document/architecture.docx",
    "content_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "scope": "agent",
}


class _Connector:
    def __init__(self, *, existing_titles: dict[str, str] | None = None, body: str = ""):
        self.calls: list[tuple[str, dict]] = []
        self.existing = existing_titles or {}  # title → page id
        self.body = body

    async def read_adapter(self, operation, **kwargs):
        self.calls.append((operation, kwargs))
        if operation == "list_pages":
            title = kwargs.get("title", "")
            if title in self.existing:
                return [{"id": self.existing[title], "title": title, "status": "current",
                         "url": f"/spaces/QUICKLINK/pages/{self.existing[title]}/{title}"}]
            return []
        if operation == "fetch_page_detail":
            return {"id": kwargs["page_id"], "title": "Design Documents", "version": 3,
                    "content": self.body}
        raise AssertionError(f"unexpected read {operation}")

    async def write_adapter(self, operation, **kwargs):
        self.calls.append((operation, kwargs))
        if operation == "create_page":
            return {"id": "1310721", "url": "/spaces/QUICKLINK/pages/1310721/architecture"}
        if operation == "upload_attachment":
            return {"id": "att1", "title": kwargs["filename"]}
        if operation == "update_page":
            return {"id": kwargs["page_id"], "version": 2}
        raise AssertionError(f"unexpected write {operation}")


@pytest.fixture
def tools(monkeypatch):
    holder = {}

    async def _resolve(agent_id):
        return (holder["connector"], "tenant", "project"), ""

    async def _approved(tenant_id, project_id, stage):
        return [dict(_DOC)]

    async def _download(blob_path):
        return b"PK\x03\x04 the docx bytes"

    monkeypatch.setattr(ca, "_resolve", _resolve)
    from shared.tools import sharepoint_artifacts as sp
    monkeypatch.setattr(sp, "_approved_documents", _approved)
    monkeypatch.setattr(sp, "_download", _download)
    made = {t.name: t for t in ca.make_confluence_tools(agent_id="design", stage="design")}

    def _with(connector):
        holder["connector"] = connector
        return made

    return _with


async def test_a_second_publish_updates_the_existing_page_instead_of_failing(tools):
    connector = _Connector(existing_titles={"architecture": "1310721"})
    made = tools(connector)

    out = await made["publish_approved_to_confluence"].ainvoke({"space": "QUICKLINK", "filename": "architecture.docx"})

    writes = [op for op, _ in connector.calls if op in ("create_page", "upload_attachment", "update_page")]
    assert writes == ["upload_attachment", "update_page"], writes
    assert "Published 1 approved document" in out
    assert "updated the existing page" in out
    assert "ERROR" not in out
    update = next(kw for op, kw in connector.calls if op == "update_page")
    assert update["page_id"] == "1310721"
    assert 'ri:filename="architecture.docx"' in update["content"]


async def test_a_first_publish_still_creates_the_page(tools):
    connector = _Connector()
    made = tools(connector)

    out = await made["publish_approved_to_confluence"].ainvoke({"space": "QUICKLINK", "filename": "architecture.docx"})

    writes = [op for op, _ in connector.calls if op in ("create_page", "upload_attachment", "update_page")]
    assert writes == ["create_page", "upload_attachment", "update_page"], writes
    assert "updated the existing page" not in out


async def test_attaching_a_file_makes_the_page_show_it(tools):
    connector = _Connector(body="<p>Design documentation for the QuickLink project.</p>")
    made = tools(connector)

    out = await made["attach_file_to_confluence_page"].ainvoke({"page_id": "1474561", "filename": "architecture.docx"})

    assert "the page now shows the file" in out
    update = next(kw for op, kw in connector.calls if op == "update_page")
    assert update["page_id"] == "1474561"
    assert update["title"] == "Design Documents", "v2 refuses an update without the title"
    assert update["version"] == 4, "the next version, from the page just read"
    assert update["content"].startswith("<p>Design documentation for the QuickLink project.</p>")
    assert 'ri:filename="architecture.docx"' in update["content"]


async def test_attaching_the_same_file_twice_does_not_add_a_second_card(tools):
    connector = _Connector(body="<p>Notes</p>" + ca.attachment_card("architecture.docx"))
    made = tools(connector)

    out = await made["attach_file_to_confluence_page"].ainvoke({"page_id": "1474561", "filename": "architecture.docx"})

    assert "now shows the file" in out
    assert not any(op == "update_page" for op, _ in connector.calls), "the card is already there"


# ── the connector: re-uploading a filename versions the attachment ───────────


class _Resp:
    def __init__(self, status: int, text: str = ""):
        self.status_code = status
        self.text = text


async def test_reuploading_a_filename_versions_the_existing_attachment(monkeypatch):
    from config.connectors.confluence import ConfluenceConnector

    c = ConfluenceConnector(org_url="https://x.atlassian.net", tenant_id="tenant")
    calls = []

    async def _request(method, path, tenant_id="", v1=False, **kwargs):
        calls.append((method, path))
        if method == "POST" and path.endswith("/child/attachment"):
            req = httpx.Request("POST", "https://x")
            resp = httpx.Response(400, request=req, text='{"message":"Cannot add a new attachment with same file name as an existing attachment: architecture.docx"}')
            raise httpx.HTTPStatusError("400", request=req, response=resp)
        if method == "GET" and path.endswith("/child/attachment"):
            return {"results": [{"id": "att950273", "title": "architecture.docx"}]}, 0
        if method == "POST" and path.endswith("/att950273/data"):
            return {"id": "att950273", "title": "architecture.docx", "version": {"number": 2}}, 0
        raise AssertionError((method, path))

    monkeypatch.setattr(c, "_confluence_request_with_retry", _request)
    out = await c.upload_attachment("1474561", "architecture.docx", b"bytes", "application/octet-stream")

    assert out["id"] == "att950273"
    assert [p for _, p in calls] == [
        "/content/1474561/child/attachment",
        "/content/1474561/child/attachment",
        "/content/1474561/child/attachment/att950273/data",
    ]


async def test_a_400_that_is_not_a_duplicate_still_raises(monkeypatch):
    from config.connectors.confluence import ConfluenceConnector

    c = ConfluenceConnector(org_url="https://x.atlassian.net", tenant_id="tenant")

    async def _request(method, path, tenant_id="", v1=False, **kwargs):
        if method == "POST":
            req = httpx.Request("POST", "https://x")
            resp = httpx.Response(400, request=req, text="bad multipart")
            raise httpx.HTTPStatusError("400", request=req, response=resp)
        return {"results": []}, 0  # no attachment of that name exists

    monkeypatch.setattr(c, "_confluence_request_with_retry", _request)
    with pytest.raises(httpx.HTTPStatusError):
        await c.upload_attachment("1", "x.docx", b"bytes")
