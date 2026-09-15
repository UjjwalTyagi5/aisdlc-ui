"""Blob storage on the local filesystem — the same six methods, one directory.

WHY THIS EXISTS. Every generated document — a BRD, a design, a QA report — is stored
as bytes behind `AZURE_BLOB_ACCOUNT_URL` and referenced by its blob name from an
`artifacts` row. When that storage account is gone, or was never provisioned, the row
survives and nothing can read it: approval cannot promote it, the download route
answers 502, and `read_document` tells the agent the file "could not be retrieved".
Local development needs the whole lifecycle to work without a cloud account.

THE CONTRACT IS `BlobStorageClient`'s, method for method, because every caller —
`artifact_store.store_artifact`, the approval route's `move_blob`, the download route,
`artifact_consumption._download`, the SharePoint and Confluence publishers — talks to
"the blob client" and never to Azure. Where the Azure client raises for a missing blob,
this raises `FileNotFoundError`; where it returns False for a delete of nothing, so does
this. Callers catch broadly, so the exception type is not load-bearing, but keeping the
shapes identical is what lets the factory swap one for the other unnoticed.

THE BLOB NAME IS THE ONLY ISOLATION. Blob storage is a flat namespace whose first
segment is the tenant id, and `is_blob_path` tests exactly that prefix. On a
filesystem a `..` inside a name would walk out of the tenant's tree and out of the
root, so every name is resolved and checked to lie under `root` before any I/O — and
refused with `ValueError` otherwise. `artifact_store.blob_path_for` sanitises every
segment already; this is the second lock on the same door.

NOT FOR PRODUCTION. One directory on one machine has no replication, no access
policy and no SAS links (evidence export answers 503 on this backend). It is the
local-dev and demo backend, selected by `ARTIFACT_STORAGE_ROOT` in `.env`.
"""
from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)


class LocalBlobStorageClient:
    """`BlobStorageClient`'s six methods, backed by files under `root`."""

    #: Evidence export mints Azure user-delegation SAS links; there is nothing
    #: equivalent to mint here, and the router checks this rather than the class.
    supports_sas_urls = False

    def __init__(self, root: str | os.PathLike[str]) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    # ── the path guard ───────────────────────────────────────────────────────

    def _path_for(self, blob_name: str) -> Path:
        """The file for `blob_name`, or `ValueError` when the name would leave `root`.

        Absolute names, drive letters and any `..` that resolves upward are refused
        outright rather than normalised: a caller handing over such a name is a bug
        (or an attack), and quietly relocating its file inside the root would hide it.
        """
        name = (blob_name or "").strip()
        if not name or name.startswith(("/", "\\")) or os.path.isabs(name) or ":" in name:
            raise ValueError(f"blob name is not a relative path: {blob_name!r}")
        candidate = (self.root / name).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError:
            raise ValueError(f"blob name escapes the storage root: {blob_name!r}") from None
        return candidate

    # ── the contract ─────────────────────────────────────────────────────────

    async def upload_bytes(
        self,
        data: bytes,
        blob_name: str,
        content_type: str = "application/octet-stream",
        overwrite: bool = True,
    ) -> str:
        """Write `data` under `blob_name` and return its URL.

        ATOMIC: written to a temporary file beside the target and renamed over it, so
        a reader — the approval route's verification read, a concurrent download —
        never sees a half-written document. `content_type` is recorded on the artifact
        row, not here; a filesystem has nowhere to keep it.
        """
        path = self._path_for(blob_name)
        if not overwrite and path.exists():
            raise FileExistsError(f"blob already exists: {blob_name}")

        def _write() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix=".upload-", dir=path.parent)
            try:
                with os.fdopen(fd, "wb") as fh:
                    fh.write(data)
                os.replace(tmp, path)
            except BaseException:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise

        await asyncio.to_thread(_write)
        return self.get_url(blob_name)

    async def download_bytes(self, blob_name: str) -> bytes:
        """The stored bytes. Raises `FileNotFoundError` when there are none, as the
        Azure client raises for a missing blob — never empty bytes, which every
        extractor would read as an empty document."""
        path = self._path_for(blob_name)
        return await asyncio.to_thread(path.read_bytes)

    async def delete_blob(self, blob_name: str) -> bool:
        """Remove the file. True if it was there, False if it was not — never an error
        for a missing file, so a row whose upload failed stays deletable."""
        path = self._path_for(blob_name)

        def _delete() -> bool:
            try:
                path.unlink()
            except FileNotFoundError:
                return False
            return True

        return await asyncio.to_thread(_delete)

    async def move_blob(self, src: str, dst: str, content_type: str | None = None) -> str:
        """Copy `src` to `dst`, verify, then delete `src` — the approval promotion.

        The same three steps in the same order as the Azure client, because the
        property approval depends on is the order: the source is removed only after
        the destination has been read back and compared, so a failure leaves two
        copies (recoverable) rather than none.
        """
        data = await self.download_bytes(src)
        url = await self.upload_bytes(
            data, dst, content_type=content_type or "application/octet-stream"
        )
        if await self.download_bytes(dst) != data:
            raise RuntimeError("copy verification failed; source left in place")
        await self.delete_blob(src)
        return url

    def get_url(self, blob_name: str) -> str:
        """A `file://` URL for the stored file, without touching the disk.

        Non-empty on purpose: `store_artifact` records this as `blob_url`, and the
        artifact list offers a download link only when `blob_url` is set. The link
        itself goes through `/artifacts/{id}/download`, never to this URL.
        """
        return self._path_for(blob_name).as_uri()

    async def probe(self) -> str:
        """`/health`'s blob check: can this process write under the root right now?"""

        def _touch() -> None:
            fd, tmp = tempfile.mkstemp(prefix=".probe-", dir=self.root)
            os.close(fd)
            os.unlink(tmp)

        try:
            await asyncio.to_thread(_touch)
            return "ok"
        except Exception as exc:  # noqa: BLE001 — the probe reports, never raises
            return f"error: {type(exc).__name__}"

    async def close(self) -> None:
        """Nothing to close; kept so the lifespan shutdown can call it blindly."""
