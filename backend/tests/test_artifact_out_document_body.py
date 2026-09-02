"""An artifact row becomes something a person can read and download exactly once.

THREE FAULTS IN ONE MAPPING, all visible in a single screenshot of the Design page:

1. TWO DOWNLOAD CONTROLS FOR ONE FILE. The body was
   `{"kind": "raw", "markdown": "[Download artifact](<url>)"}` while `downloadUrl`
   carried the same URL — so the detail pane rendered a link and the list row rendered
   an icon, both pointing at the same place.

2. THE CARD WAS CAPTIONED "ADR · ARCHITECTURE DECISION RECORD" FOR A PDF, because a
   `raw` body is rendered by AdrViewer and AdrViewer labels everything an ADR.

3. THE LINK DID NOT WORK. `download_path` preferred `artifact.blob_url`, which points
   at `https://<account>.blob.core.windows.net/…` on an account with public access
   disabled. Following it never returns the file. The authorised path is
   `/artifacts/{id}/download`, which resolves the id through a join on `Run.tenant_id`.

And the title was the whole stored path — two UUIDs of tenant/run routing in front of
the only segment anybody reads.
"""
from __future__ import annotations

import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.routers._schemas import ArtifactOut  # noqa: E402

TENANT = uuid.uuid4()
PROJECT = uuid.uuid4()


def _artifact(*, blob_path, blob_url="https://acct.blob.core.windows.net/c/x", **kw):
    return SimpleNamespace(
        id=kw.get("id", uuid.uuid4()),
        run_id=uuid.uuid4(),
        tenant_id=TENANT,
        artifact_type=kw.get("artifact_type", "document"),
        blob_url=blob_url,
        blob_path=blob_path,
        content_type=kw.get("content_type", "application/pdf"),
        size_bytes=kw.get("size_bytes", 245_000),
        created_at=datetime(2026, 9, 2, 10, 49, tzinfo=timezone.utc),
    )


def _blob_path(leaf="sdlc-password-reset-design.pdf"):
    """A path store_artifact would write — is_blob_path tests the tenant prefix."""
    return f"{TENANT}/{uuid.uuid4()}/document/{leaf}"


def _out(artifact):
    return ArtifactOut.from_orm_artifact(artifact, "design", str(PROJECT))


# -- the title is the filename -------------------------------------------------


@pytest.mark.unit
def test_the_title_is_the_filename_not_the_whole_path():
    out = _out(_artifact(blob_path=_blob_path()))
    assert out.title == "sdlc-password-reset-design.pdf"
    assert str(TENANT) not in out.title


@pytest.mark.unit
def test_an_artifact_with_no_file_still_has_a_title():
    out = _out(_artifact(blob_path=None, blob_url=None))
    assert out.title == "document"


# -- one download control ------------------------------------------------------


@pytest.mark.unit
def test_the_body_carries_no_markdown_link():
    """THE DUPLICATE. A link in the body plus the row's icon is two controls for one
    file."""
    body = _out(_artifact(blob_path=_blob_path())).body
    assert body["kind"] == "document"
    assert "markdown" not in body
    assert "Download artifact" not in str(body)


@pytest.mark.unit
def test_the_body_is_not_raw_so_it_is_not_captioned_as_an_adr():
    """`raw` routes to AdrViewer, which titles its output "Architecture Decision
    Record" — wrong for every PDF, DOCX and XLSX that lands there."""
    assert _out(_artifact(blob_path=_blob_path())).body["kind"] != "raw"


@pytest.mark.unit
def test_the_body_describes_the_file():
    body = _out(_artifact(blob_path=_blob_path())).body
    assert body["filename"] == "sdlc-password-reset-design.pdf"
    assert body["contentType"] == "application/pdf"
    assert body["sizeBytes"] == 245_000


# -- the download URL actually works -------------------------------------------


@pytest.mark.unit
def test_a_stored_blob_links_to_the_authorised_download_route_not_azure():
    """THE BROKEN LINK. The account has public access disabled, so the raw blob URL
    returns an error rather than the file, whoever clicks it."""
    art = _artifact(blob_path=_blob_path())
    out = _out(art)

    assert out.downloadUrl == f"/api/artifacts/{art.id}/download"
    assert "blob.core.windows.net" not in (out.downloadUrl or "")


@pytest.mark.unit
@pytest.mark.parametrize(
    "legacy", [r"C:\pwc_work\outputs\brd.docx", "/var/app/generated/u1/output/brd.docx"]
)
def test_a_legacy_local_file_keeps_its_static_url(legacy):
    """The download endpoint deliberately REFUSES legacy local paths, so routing them
    through it would break downloads that currently work."""
    out = _out(_artifact(blob_path=legacy, blob_url=None))
    assert out.downloadUrl == f"/generated/{legacy}"


@pytest.mark.unit
def test_an_artifact_with_no_file_has_no_download_url():
    out = _out(_artifact(blob_path=None, blob_url=None))
    assert out.downloadUrl is None


# -- the upload-failed case ----------------------------------------------------


@pytest.mark.unit
def test_a_row_whose_upload_failed_is_marked_not_stored():
    """THE CASE THIS PRODUCT ACTUALLY HIT. store_artifact degrades on an upload failure
    by writing the row with blob_url = None — and it still writes blob_path, so the path
    alone proves nothing. Only blob_url says the bytes arrived."""
    out = _out(_artifact(blob_path=_blob_path(), blob_url=None))
    assert out.body["stored"] is False
    # And no URL either — a download icon that 404s is worse than no icon.
    assert out.downloadUrl is None


@pytest.mark.unit
def test_a_successful_upload_is_marked_stored():
    assert _out(_artifact(blob_path=_blob_path())).body["stored"] is True


@pytest.mark.unit
def test_a_legacy_local_file_counts_as_stored():
    """It has no blob_url and is nonetheless on disk and downloadable."""
    out = _out(_artifact(blob_path="/var/app/generated/x/brd.docx", blob_url=None))
    assert out.body["stored"] is True


# -- the tenant prefix stays load-bearing --------------------------------------


@pytest.mark.unit
def test_another_tenants_prefix_is_not_treated_as_this_tenants_blob():
    """is_blob_path tests THIS tenant's prefix. A path under another tenant's id must
    not resolve to the authorised route, which would hand out an id that 404s at best."""
    other = f"{uuid.uuid4()}/{uuid.uuid4()}/document/x.pdf"
    out = _out(_artifact(blob_path=other, blob_url=None))
    assert not (out.downloadUrl or "").startswith("/api/artifacts/")
