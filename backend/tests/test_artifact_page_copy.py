"""A generated document's page copy travels with the document.

The app renders a document's markdown as a report. That copy used to live only under the
backend's public `/generated/` mount, fetched by a URL the chat message carried — which
the app's Content Security Policy (`connect-src 'self'`) refused ("Failed to fetch"), and
which was gone after a reload. Now the copy is stored beside the document's bytes,
follows them on approval, goes with them on rejection, and is served same-origin by
GET /artifacts/{id}/page.
"""
from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.unit

TENANT = "11111111-1111-1111-1111-111111111111"
BLOB = f"{TENANT}/bu/proj/requirements/run/document/QuickLink_BRD.docx"


class _Store:
    """Enough of a blob client: bytes by name, move and delete."""

    def __init__(self):
        self.blobs: dict[str, bytes] = {}

    async def upload_bytes(self, data, name, content_type=None):
        self.blobs[name] = data
        return f"file:///{name}"

    async def download_bytes(self, name):
        if name not in self.blobs:
            raise FileNotFoundError(name)
        return self.blobs[name]

    async def delete_blob(self, name):
        return self.blobs.pop(name, None) is not None

    async def move_blob(self, src, dst, content_type=None):
        data = await self.download_bytes(src)
        self.blobs[dst] = data
        del self.blobs[src]
        return f"file:///{dst}"


@pytest.mark.asyncio
async def test_the_copy_is_stored_pending_promoted_on_approval_and_read_from_wherever_it_is():
    from shared.services import artifact_page as ap

    store = _Store()
    assert await ap.store_page_copy(store, BLOB, "## Executive Summary\nQuickLink…") is True
    assert list(store.blobs) == [f"{TENANT}/_pending/bu/proj/requirements/run/document/QuickLink_BRD.docx.page.md"]

    assert (await ap.read_page_copy(store, BLOB, "draft")).startswith("## Executive Summary")
    assert await ap.read_page_copy(store, BLOB, "approved") is None, "not there yet"

    await ap.promote_page_copy(store, BLOB)
    assert list(store.blobs) == [f"{BLOB}.page.md"]
    assert (await ap.read_page_copy(store, BLOB, "approved")).startswith("## Executive Summary")


@pytest.mark.asyncio
async def test_rejection_and_deletion_discard_the_copy_and_a_missing_copy_is_not_an_error():
    from shared.services import artifact_page as ap

    store = _Store()
    await ap.store_page_copy(store, BLOB, "x")
    await ap.discard_page_copy(store, BLOB, "pending")
    assert store.blobs == {}

    # A document from before page copies existed: nothing to move, nothing to say.
    await ap.promote_page_copy(store, BLOB)
    await ap.discard_page_copy(store, BLOB, "approved")
    assert await ap.read_page_copy(store, BLOB, "approved") is None


def test_the_sibling_is_the_md_beside_the_docx(tmp_path):
    from shared.services.artifact_page import sibling_markdown_path

    docx = tmp_path / "QuickLink_BRD.docx"
    docx.write_bytes(b"x")
    assert sibling_markdown_path(str(docx)) is None
    (tmp_path / "QuickLink_BRD.md").write_text("## Summary", encoding="utf-8")
    assert sibling_markdown_path(str(docx)) == str(tmp_path / "QuickLink_BRD.md")
    assert sibling_markdown_path(str(tmp_path / "QuickLink_BRD.md")) is None, "an .md is not its own sibling"


@pytest.mark.asyncio
async def test_registration_stores_the_sibling_as_the_page_copy(tmp_path, monkeypatch):
    """register_generated_file keeps the .md the agent left beside the Word file."""
    from shared.services import chat_artifacts as ca

    docx = tmp_path / "QuickLink_BRD.docx"
    docx.write_bytes(b"PK")
    (tmp_path / "QuickLink_BRD.md").write_text("## Executive Summary\nHello", encoding="utf-8")
    store = _Store()
    stored_row = SimpleNamespace(id="art-1", blob_path=BLOB, blob_url=None, upload_succeeded=True)

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(ca, "get_tenant_id", lambda: TENANT)
    monkeypatch.setattr(ca, "get_project_id", lambda: "proj")
    monkeypatch.setattr(ca, "get_db_session_for_tenant", lambda _t: _Session())
    monkeypatch.setattr(ca, "_get_or_create_chat_run", AsyncMock(return_value="run-1"))
    with patch("shared.services.artifact_store.store_artifact", AsyncMock(return_value=stored_row)), \
         patch("shared.services.artifact_store.get_blob_client", lambda: store), \
         patch("shared.services.artifact_service.publish_artifact_ready", AsyncMock()):
        artifact_id = await ca.register_generated_file("QuickLink_BRD.docx", str(docx), "http://x/QuickLink_BRD.docx", stage="requirements")

    assert artifact_id == "art-1"
    page = store.blobs[f"{TENANT}/_pending/bu/proj/requirements/run/document/QuickLink_BRD.docx.page.md"]
    assert page.decode("utf-8") == "## Executive Summary\nHello"


@pytest.mark.asyncio
async def test_the_page_route_serves_the_copy_and_says_when_there_is_none(monkeypatch):
    from fastapi import HTTPException

    from shared.routers import artifacts as r

    store = _Store()
    await store.upload_bytes(b"## Summary\nHi", f"{TENANT}/_pending/bu/proj/requirements/run/document/QuickLink_BRD.docx.page.md")
    art = SimpleNamespace(id="art-1", project_id="proj", blob_path=BLOB, approval_status="draft")
    request = SimpleNamespace(state=SimpleNamespace(tenant_id=TENANT, user_id="u1", permissions=[]),
                              app=SimpleNamespace(state=SimpleNamespace(blob_client=store)))
    monkeypatch.setattr(r, "_get_artifact_or_404", AsyncMock(return_value=(art, None)))
    monkeypatch.setattr(r, "_assert_project_visible", AsyncMock())

    out = await r.artifact_page("art-1", request, db=None)
    assert out == {"artifactId": "art-1", "filename": "QuickLink_BRD.docx", "status": "draft", "markdown": "## Summary\nHi"}

    art.approval_status = "approved"  # bytes moved, copy not yet — an old document
    with pytest.raises(HTTPException) as exc:
        await r.artifact_page("art-1", request, db=None)
    assert exc.value.status_code == 404 and "no page view" in exc.value.detail

    art.approval_status = "rejected"
    with pytest.raises(HTTPException) as exc:
        await r.artifact_page("art-1", request, db=None)
    assert exc.value.status_code == 410
