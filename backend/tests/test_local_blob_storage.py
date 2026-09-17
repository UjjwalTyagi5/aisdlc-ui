"""A local-directory stand-in for Azure Blob Storage, and the factory that picks it.

WHY. The Azure storage account behind `AZURE_BLOB_ACCOUNT_URL` was terminated, and with
it every generated document's bytes. Local development still needs generated documents
to be stored, approved, downloaded and read by agents — the whole artifact lifecycle —
without a cloud account. `LocalBlobStorageClient` gives `artifact_store`, the approval
route, the download route and the agents' `read_document` the same six methods they
already call, backed by files under one root.

What is pinned:

  * the round trip — bytes uploaded under a blob name come back identical, and the row
    gets a non-empty URL, which is what makes the UI offer a download link;
  * a missing file raises (the callers already treat any exception as "unavailable")
    and a delete of a missing file returns False, exactly like the Azure client;
  * `move_blob` — what approval relies on — copies, verifies, then removes the source;
  * a blob name that escapes the root is refused, because the tenant prefix on the
    blob name is the only isolation there is and a `..` would walk past it;
  * `build_blob_client` prefers Azure when it is configured, falls back to the local
    root when that is configured instead, and returns None when neither is.
"""
from __future__ import annotations

import pytest

from shared.storage.local_blob import LocalBlobStorageClient

_TENANT = "22222222-2222-2222-2222-222222222222"
_NAME = f"{_TENANT}/unit/project/requirements/run/document/brd.docx"


@pytest.fixture
def client(tmp_path):
    return LocalBlobStorageClient(tmp_path / "store")


async def test_bytes_round_trip_and_the_row_gets_a_url(client):
    url = await client.upload_bytes(b"hello brd", _NAME, content_type="application/x")
    assert url, "an empty URL means the row records blob_url=None and no download link"
    assert await client.download_bytes(_NAME) == b"hello brd"
    assert client.get_url(_NAME) == url


async def test_the_file_lands_under_the_root_mirroring_the_blob_path(client, tmp_path):
    await client.upload_bytes(b"x", _NAME)
    assert (tmp_path / "store" / _TENANT / "unit" / "project" / "requirements" /
            "run" / "document" / "brd.docx").read_bytes() == b"x"


async def test_overwrite_is_the_default_like_azure(client):
    await client.upload_bytes(b"one", _NAME)
    await client.upload_bytes(b"two", _NAME)
    assert await client.download_bytes(_NAME) == b"two"


async def test_overwrite_false_refuses_to_replace(client):
    await client.upload_bytes(b"one", _NAME)
    with pytest.raises(FileExistsError):
        await client.upload_bytes(b"two", _NAME, overwrite=False)
    assert await client.download_bytes(_NAME) == b"one"


async def test_a_missing_file_raises_rather_than_returning_empty_bytes(client):
    """Every caller catches the exception and reports the document unavailable. Empty
    bytes would be extracted as an empty document and reported as read."""
    with pytest.raises(FileNotFoundError):
        await client.download_bytes(_NAME)


async def test_deleting_a_missing_file_is_false_not_an_error(client):
    """The Azure client's contract, kept: a row whose upload failed must still be
    deletable, and the caller uses False to know nothing was there."""
    assert await client.delete_blob(_NAME) is False
    await client.upload_bytes(b"x", _NAME)
    assert await client.delete_blob(_NAME) is True
    with pytest.raises(FileNotFoundError):
        await client.download_bytes(_NAME)


async def test_move_copies_verifies_and_then_removes_the_source(client):
    """Approval promotes a document out of `_pending`. The source must be gone AFTER
    the destination is readable, never before."""
    src = f"{_TENANT}/_pending/unit/project/requirements/run/document/brd.docx"
    await client.upload_bytes(b"approved bytes", src)

    url = await client.move_blob(src, _NAME, content_type="application/x")

    assert url == client.get_url(_NAME)
    assert await client.download_bytes(_NAME) == b"approved bytes"
    with pytest.raises(FileNotFoundError):
        await client.download_bytes(src)


@pytest.mark.parametrize("name", [
    "../outside.bin",
    f"{_TENANT}/../../outside.bin",
    "/etc/passwd",
    "C:/Windows/system32/x",
    "",
])
async def test_a_name_that_escapes_the_root_is_refused(client, tmp_path, name):
    with pytest.raises(ValueError):
        await client.upload_bytes(b"x", name)
    with pytest.raises(ValueError):
        await client.download_bytes(name)
    assert not (tmp_path / "outside.bin").exists()


async def test_the_probe_says_ok_when_the_root_is_writable(client):
    assert await client.probe() == "ok"


async def test_close_is_a_no_op(client):
    await client.close()


# ── the factory ─────────────────────────────────────────────────────────────


def test_the_factory_prefers_azure_when_it_is_configured(monkeypatch, tmp_path):
    from shared import storage

    built = {}

    class _FakeAzure:
        def __init__(self):
            built["azure"] = True

    monkeypatch.setattr(storage, "AZURE_BLOB_ACCOUNT_URL", "https://acct.blob.core.windows.net/")
    monkeypatch.setattr(storage, "ARTIFACT_STORAGE_ROOT", str(tmp_path))
    monkeypatch.setattr(storage, "BlobStorageClient", _FakeAzure)

    assert isinstance(storage.build_blob_client(), _FakeAzure)


def test_the_factory_falls_back_to_the_local_root(monkeypatch, tmp_path):
    from shared import storage

    monkeypatch.setattr(storage, "AZURE_BLOB_ACCOUNT_URL", "")
    monkeypatch.setattr(storage, "ARTIFACT_STORAGE_ROOT", str(tmp_path / "artifact-store"))

    client = storage.build_blob_client()

    assert isinstance(client, LocalBlobStorageClient)
    assert client.root == (tmp_path / "artifact-store").resolve()


def test_the_factory_returns_none_when_neither_is_configured(monkeypatch):
    from shared import storage

    monkeypatch.setattr(storage, "AZURE_BLOB_ACCOUNT_URL", "")
    monkeypatch.setattr(storage, "ARTIFACT_STORAGE_ROOT", "")

    assert storage.build_blob_client() is None


def test_a_relative_root_is_resolved_against_the_backend_directory(monkeypatch):
    """`.env` says `files/artifact-store`; that must mean backend/files/artifact-store
    whatever directory uvicorn was started from."""
    from config.env import BACKEND_ROOT
    from shared import storage

    monkeypatch.setattr(storage, "AZURE_BLOB_ACCOUNT_URL", "")
    monkeypatch.setattr(storage, "ARTIFACT_STORAGE_ROOT", "files/artifact-store")

    client = storage.build_blob_client()

    assert client.root == (BACKEND_ROOT / "files" / "artifact-store").resolve()
