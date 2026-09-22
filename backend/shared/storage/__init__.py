"""Where generated documents' bytes live, and the one place that decides which backend.

Two backends share one contract (see `local_blob.py` for the contract's terms):

  * `BlobStorageClient`      — Azure Blob Storage, when `AZURE_BLOB_ACCOUNT_URL` is set;
  * `LocalBlobStorageClient` — a directory, when `ARTIFACT_STORAGE_ROOT` is set instead.

`build_blob_client` is called from exactly two places — the FastAPI lifespan (which
stores the client on `app.state` for the routes and the health probe) and
`artifact_store.get_blob_client` (for agent tool code that runs below any request) —
and both used to test `AZURE_BLOB_ACCOUNT_URL` themselves. Deciding here means the
two cannot disagree about which storage the process is using.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from config.env import ARTIFACT_STORAGE_ROOT, AZURE_BLOB_ACCOUNT_URL, BACKEND_ROOT

from .azure_blob import BlobStorageClient
from .local_blob import LocalBlobStorageClient

__all__ = ["BlobStorageClient", "LocalBlobStorageClient", "build_blob_client"]


def build_blob_client() -> Any:
    """The configured storage client, or None when no storage is configured.

    Azure wins when both are set: a deployment that has a storage account is not
    running a demo, and a stray local root must not silently divert its documents
    onto one machine's disk. A relative local root is resolved against `backend/`,
    whatever directory the process was started from.
    """
    if AZURE_BLOB_ACCOUNT_URL:
        return BlobStorageClient()
    if ARTIFACT_STORAGE_ROOT:
        root = Path(ARTIFACT_STORAGE_ROOT)
        if not root.is_absolute():
            root = BACKEND_ROOT / root
        return LocalBlobStorageClient(root)
    return None
