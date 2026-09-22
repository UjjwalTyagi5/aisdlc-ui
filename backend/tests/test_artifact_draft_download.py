"""A document downloads before it is approved — only a rejected one does not.

LIVE (2026-09-21): the Download button appeared only after approval. `ArtifactOut` gave a
`downloadUrl` to approved documents alone and `/artifacts/{id}/download` answered 409 to
everything else — telling a DRAFT it "was rejected and its file has been deleted". So the
Word file or workbook an agent had just written could not be taken away to review or edit
before raising it, which is the reason to download it at all.

A draft or pending file is served from the tenant's `_pending` prefix, where it lives until
approval moves it; approval still decides what joins the project's record.
"""
from __future__ import annotations

import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.routers._schemas import ArtifactOut  # noqa: E402
from shared.services.artifact_store import pending_blob_path  # noqa: E402

TENANT = uuid.uuid4()
PATH = f"{TENANT}/{uuid.uuid4()}/design/{uuid.uuid4()}/document/architecture.docx"


_URL = "https://acct.blob.core.windows.net/c/x"


def _row(status: str, *, blob_url: str | None = "unset"):
    # As store_artifact writes them: `blob_url` is set by APPROVAL, so every draft and
    # pending row has none — which is what hid their link at first, even after the fix.
    if blob_url == "unset":
        blob_url = _URL if status == "approved" else None
    return SimpleNamespace(
        id=uuid.uuid4(), run_id=uuid.uuid4(), project_id=uuid.uuid4(), tenant_id=TENANT,
        artifact_type="document", blob_url=blob_url, blob_path=PATH, approval_status=status,
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        size_bytes=1200, stage="design", created_at=datetime(2026, 9, 21, tzinfo=timezone.utc),
    )


# ── the link ─────────────────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.parametrize("status", ["draft", "pending", "approved"])
def test_a_stored_document_offers_its_download_whatever_its_standing(status):
    out = ArtifactOut.from_orm_artifact(_row(status), "design", "p")
    assert out.downloadUrl == f"/api/artifacts/{out.id}/download"
    assert out.body["stored"] is True


@pytest.mark.unit
def test_a_rejected_document_and_a_failed_promotion_offer_none():
    rejected = ArtifactOut.from_orm_artifact(_row("rejected"), "design", "p")
    assert rejected.downloadUrl is None and rejected.body["rejected"] is True
    # Approved, but approval never reached the final path — the one state where a missing
    # `blob_url` does mean the file is not there.
    failed = ArtifactOut.from_orm_artifact(_row("approved", blob_url=None), "design", "p")
    assert failed.downloadUrl is None and failed.body["stored"] is False


# ── the route ────────────────────────────────────────────────────────────────


class _Store:
    def __init__(self, blobs: dict[str, bytes]):
        self.blobs, self.read = blobs, []

    async def download_bytes(self, name):
        self.read.append(name)
        if name not in self.blobs:
            raise FileNotFoundError(name)
        return self.blobs[name]


async def _download(monkeypatch, status: str, blobs: dict):
    from shared.routers import artifacts as r

    row = _row(status)
    store = _Store(blobs)
    request = SimpleNamespace(state=SimpleNamespace(tenant_id=str(TENANT), user_id="u1", permissions=[]),
                              app=SimpleNamespace(state=SimpleNamespace(blob_client=store)))
    monkeypatch.setattr(r, "_get_artifact_or_404", AsyncMock(return_value=(row, None)))
    monkeypatch.setattr(r, "_assert_project_visible", AsyncMock())
    return await r.download_artifact(str(row.id), request, db=None), store


@pytest.mark.parametrize("status", ["draft", "pending"])
async def test_a_draft_or_pending_file_downloads_from_the_pending_area(monkeypatch, status):
    response, store = await _download(monkeypatch, status, {pending_blob_path(PATH): b"PK draft"})
    assert response.status_code == 200 and response.body == b"PK draft"
    assert response.headers["content-disposition"] == 'attachment; filename="architecture.docx"'
    assert store.read[0] == pending_blob_path(PATH)


async def test_an_approved_file_downloads_from_its_final_path_only(monkeypatch):
    response, store = await _download(monkeypatch, "approved", {PATH: b"PK final", pending_blob_path(PATH): b"stale"})
    assert response.body == b"PK final" and store.read == [PATH]


async def test_a_rejected_file_is_refused_and_a_lost_one_says_so(monkeypatch):
    with pytest.raises(HTTPException) as rejected:
        await _download(monkeypatch, "rejected", {})
    assert rejected.value.status_code == 409 and "rejected" in rejected.value.detail

    with pytest.raises(HTTPException) as lost:
        await _download(monkeypatch, "draft", {})
    assert lost.value.status_code == 502
