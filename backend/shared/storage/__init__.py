"""Where generated documents' bytes live, and the one place that decides which backend.

Two backends share one contract (see `local_blob.py` for the contract's terms):

  * `BlobStorageClient`      — Azure Blob Storage, when `AZURE_BLOB_ACCOUNT_URL` is set;
  * `LocalBlobStorageClient` — a directory, when `ARTIFACT_STORAGE_ROOT` is set instead.

`build_blob_client` is called from exactly two places — the FastAPI lifespan (which
stores the client on `app.state` for the routes and the health probe) and
`artifact_store.get_blob_client` (for agent tool code that runs below any request) —
and both used to test `AZURE_BLOB_ACCOUNT_URL` themselves. Deciding here means the
two cannot disagree about which storage the process is using.

WHICH ONE, in order:

  1. `STORAGE_BACKEND=local` or `=azure` — an explicit answer, honoured whatever else is
     set. It is how you switch without deleting the other backend's settings, and how a
     dev box keeps a cloud account configured while writing to disk.
  2. `STORAGE_BACKEND=auto` (the default) with `ENV=dev`: the local root when one is set.
     A developer who has both configured means the directory — the cloud account is there
     for the deployed environments, and writing a demo's documents into it splits the
     project's files across two backends, which is how earlier switches lost track of
     approved documents.
  3. `auto` anywhere else: Azure when it is configured, because a deployment that has a
     storage account is not running a demo and its documents must not land on one
     machine's disk.
  4. Whatever is left: the local root, else nothing.

SWITCHING BACKENDS DOES NOT MOVE THE BYTES. The two hold the same names but not the same
files, so the documents already stored in the one you are leaving become unreadable:
downloads 404, previews report a missing file, and an agent asking for an approved
document is told it could not be retrieved. Run `scripts/mirror_artifact_storage.py`
before a switch, in whichever direction, and the destination will hold everything.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from config.env import (
    ARTIFACT_STORAGE_ROOT,
    AZURE_BLOB_ACCOUNT_URL,
    BACKEND_ROOT,
    ENV,
    STORAGE_BACKEND,
)

from .azure_blob import BlobStorageClient
from .local_blob import LocalBlobStorageClient

__all__ = ["BlobStorageClient", "LocalBlobStorageClient", "build_blob_client"]

logger = logging.getLogger(__name__)


def _local() -> LocalBlobStorageClient | None:
    """The local client, or None (with a reason logged) when no root is configured.

    A relative root is resolved against `backend/`, whatever directory the process was
    started from."""
    if not ARTIFACT_STORAGE_ROOT:
        return None
    root = Path(ARTIFACT_STORAGE_ROOT)
    if not root.is_absolute():
        root = BACKEND_ROOT / root
    return LocalBlobStorageClient(root)


def build_blob_client() -> Any:
    """The configured storage client, or None when no storage is configured."""
    wanted = (STORAGE_BACKEND or "auto").strip().lower()

    if wanted == "local":
        client = _local()
        if client is None:
            # Explicit, not silent: asking for local storage without saying where is a
            # misconfiguration, and falling back to Azure would write a demo's documents
            # into a cloud account nobody asked for.
            logger.error("STORAGE_BACKEND=local but ARTIFACT_STORAGE_ROOT is not set — "
                         "no artifact storage is configured.")
        return client

    if wanted == "azure":
        if AZURE_BLOB_ACCOUNT_URL:
            return BlobStorageClient()
        logger.error("STORAGE_BACKEND=azure but AZURE_BLOB_ACCOUNT_URL is not set — "
                     "no artifact storage is configured.")
        return None

    if wanted != "auto":
        logger.warning("STORAGE_BACKEND=%r is not one of auto/local/azure — treating it as auto.", wanted)

    if ENV == "dev":
        client = _local()
        if client is not None:
            return client

    if AZURE_BLOB_ACCOUNT_URL:
        return BlobStorageClient()
    return _local()
