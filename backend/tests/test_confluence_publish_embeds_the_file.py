"""A published document is VISIBLE on its Confluence page, not just attached to it.

THE FAILURE. `publish_approved_to_confluence` created the page, uploaded the .docx as an
attachment, and reported success — all true. The page body said "The approved file is
attached to this page" and showed nothing else, because Confluence keeps attachments
under the page's ⋯ menu unless the body refers to them. The user opened the page, saw
prose and no file, and asked where the document went.

What is pinned, by driving the REAL tool against a recording connector:

  * after the attachment lands, the page body is updated to link it — a
    `<ri:attachment ri:filename=…>` link is what Confluence renders as a file card;
  * the order is create → attach → update, so the body never links a file that is not
    there yet;
  * when attaching fails, the body is left saying so rather than promising a file;
  * `as_attachment=False` neither attaches nor rewrites the body.
"""
from __future__ import annotations

import pytest

from shared.tools import confluence_artifacts as ca

_DOC = {
    "id": "eee5170d-ab90-4d50-998a-cd6def280e2e",
    "name": "QuickLink_BRD_new.docx",
    "blob_path": "t/u/p/requirements/r/document/QuickLink_BRD_new.docx",
    "content_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "scope": "agent",
}


class _Connector:
    """Records every write; answers like Confluence does."""

    def __init__(self, *, attach_fails: bool = False):
        self.calls: list[tuple[str, dict]] = []
        self.attach_fails = attach_fails

    async def write_adapter(self, operation, **kwargs):
        self.calls.append((operation, kwargs))
        if operation == "create_page":
            return {"id": "917505", "url": "/spaces/QUICKLINK/pages/917505/QuickLink_BRD_new"}
        if operation == "upload_attachment":
            if self.attach_fails:
                raise RuntimeError("boom")
            return {"id": "att950273", "title": kwargs["filename"]}
        if operation == "update_page":
            return {"id": kwargs["page_id"], "version": 2}
        raise AssertionError(f"unexpected write {operation}")


@pytest.fixture
def publish(monkeypatch):
    """The real `publish_approved_to_confluence`, with only I/O replaced."""
    connector = _Connector()

    async def _resolve(agent_id):
        return (connector, "tenant", "project"), ""

    async def _approved(tenant_id, project_id, stage):
        return [dict(_DOC)]

    async def _download(blob_path):
        return b"PK\x03\x04 the docx bytes"

    monkeypatch.setattr(ca, "_resolve", _resolve)
    from shared.tools import sharepoint_artifacts as sp
    monkeypatch.setattr(sp, "_approved_documents", _approved)
    monkeypatch.setattr(sp, "_download", _download)

    tools = {t.name: t for t in ca.make_confluence_tools(agent_id="requirements", stage="requirements")}
    return connector, tools["publish_approved_to_confluence"]


async def test_the_page_body_links_the_attachment_after_it_lands(publish):
    connector, tool = publish

    out = await tool.ainvoke({"space": "QUICKLINK", "filename": "QuickLink_BRD_new.docx"})

    ops = [op for op, _ in connector.calls]
    assert ops == ["create_page", "upload_attachment", "update_page"], ops
    body = connector.calls[2][1]["content"]
    assert 'ri:filename="QuickLink_BRD_new.docx"' in body
    assert "ri:attachment" in body
    assert connector.calls[2][1]["page_id"] == "917505"
    assert "Published 1 approved document" in out


async def test_the_initial_body_does_not_promise_a_file_that_is_not_there_yet(publish):
    connector, tool = publish

    await tool.ainvoke({"space": "QUICKLINK", "filename": "QuickLink_BRD_new.docx"})

    first_body = connector.calls[0][1]["content"]
    assert "ri:attachment" not in first_body


async def test_a_failed_attachment_leaves_an_honest_body(monkeypatch, publish):
    connector, tool = publish
    connector.attach_fails = True

    out = await tool.ainvoke({"space": "QUICKLINK", "filename": "QuickLink_BRD_new.docx"})

    ops = [op for op, _ in connector.calls]
    assert ops == ["create_page", "upload_attachment", "update_page"], ops
    body = connector.calls[2][1]["content"]
    assert "ri:attachment" not in body
    assert "could not be attached" in body
    assert "attaching the file failed" in out


async def test_no_attachment_means_no_rewrite(publish):
    connector, tool = publish

    await tool.ainvoke({
        "space": "QUICKLINK", "filename": "QuickLink_BRD_new.docx", "as_attachment": False,
    })

    assert [op for op, _ in connector.calls] == ["create_page"]
    assert "ri:attachment" not in connector.calls[0][1]["content"]
    assert "attached" not in connector.calls[0][1]["content"].lower()


def test_the_body_helper_escapes_the_filename():
    """A filename is a model-chosen string that lands inside XML attributes."""
    body = ca.page_body('a"b<c>.docx', attached=True)
    assert 'ri:filename="a&quot;b&lt;c&gt;.docx"' in body
