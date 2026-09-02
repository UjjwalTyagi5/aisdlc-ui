"""Async Azure Blob Storage wrapper.

One instance per FastAPI app lifetime — store on app.state, close in lifespan shutdown.

SAS URL generation (evidence export — REQ-M8-07, T-M8-15):
  generate_evidence_sas_url() uses the user-delegation path
  (get_user_delegation_key → generate_blob_sas(user_delegation_key=...)) because
  DefaultAzureCredential cannot sign a shared-key SAS (RESEARCH Pitfall 3).
  Never pass account_key= to generate_blob_sas — that would require storing the
  storage account key in application code or environment variables, violating the
  Key Vault convention.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from azure.storage.blob import BlobSasPermissions, ContentSettings, generate_blob_sas
from azure.storage.blob.aio import BlobServiceClient

from config.env import AZURE_BLOB_ACCOUNT_URL
from shared.azure_credential import get_azure_credential

logger = logging.getLogger(__name__)

_DEFAULT_CONTAINER = "sdlc-artifacts"


class BlobStorageClient:
    """Async Azure Blob Storage wrapper. One instance per FastAPI app lifetime — store on app.state, close in lifespan shutdown."""

    def __init__(
        self,
        account_url: str = AZURE_BLOB_ACCOUNT_URL,
        container: str = _DEFAULT_CONTAINER,
    ) -> None:
        self._credential = get_azure_credential()  # shared, do not close
        self._client = BlobServiceClient(
            account_url=account_url, credential=self._credential
        )
        self._container = container

    async def upload_bytes(
        self,
        data: bytes,
        blob_name: str,
        content_type: str = "application/octet-stream",
        overwrite: bool = True,
    ) -> str:
        """Upload bytes to blob storage.

        blob_name must follow the {tenant_id}/{run_id}/{artifact_type}/{filename}
        convention — this convention enforces tenant path isolation; never accept
        blob_name directly from user input.

        Returns the URL of the uploaded blob.
        """
        container_client = self._client.get_container_client(self._container)
        blob_client = container_client.get_blob_client(blob_name)
        await blob_client.upload_blob(
            data,
            blob_type="BlockBlob",
            content_settings=ContentSettings(content_type=content_type),
            overwrite=overwrite,
        )
        return blob_client.url

    async def download_bytes(self, blob_name: str) -> bytes:
        """Download blob content as bytes."""
        container_client = self._client.get_container_client(self._container)
        blob_client = container_client.get_blob_client(blob_name)
        stream = await blob_client.download_blob()
        return await stream.readall()

    async def delete_blob(self, blob_name: str) -> bool:
        """Delete a blob. Returns True if it was removed, False if it was not there.

        A MISSING BLOB IS NOT AN ERROR HERE. The caller deleting an artifact wants the
        bytes gone; a blob that never existed — because the upload failed and the row was
        recorded anyway, which is exactly the case that motivated the delete feature —
        already satisfies that. Raising would leave the row undeletable precisely when it
        is most useless.

        Every OTHER failure (permission, network, an account outage) DOES raise, so the
        caller can refuse to delete the row and avoid orphaning bytes it cannot reach.
        """
        from azure.core.exceptions import ResourceNotFoundError

        container_client = self._client.get_container_client(self._container)
        blob_client = container_client.get_blob_client(blob_name)
        try:
            await blob_client.delete_blob()
            return True
        except ResourceNotFoundError:
            return False

    async def move_blob(self, src: str, dst: str, content_type: str | None = None) -> str:
        """Copy `src` to `dst`, verify, then delete `src`. Returns the new blob's URL.

        USED BY APPROVAL, to promote a document out of the tenant's `_pending` prefix
        into the project's real hierarchy path.

        DOWNLOAD-AND-REUPLOAD RATHER THAN A SERVER-SIDE COPY. `start_copy_from_url`
        would avoid pulling the bytes through this process, but it needs the source
        readable by URL — meaning a user-delegation SAS minted per move, and a copy
        whose completion is asynchronous and has to be polled. These are documents:
        tens of kilobytes to a few megabytes. The simpler path is worth more than the
        saved round trip, and it is synchronous, so the caller knows the bytes landed
        before it updates the row.

        THE SOURCE IS DELETED LAST, and only after the destination is read back and
        compared. A failed verification leaves both copies, which is recoverable; a
        source deleted against an unverified copy is not.
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
        """Return the blob URL without making a network call."""
        blob_client = self._client.get_blob_client(
            container=self._container, blob=blob_name
        )
        return blob_client.url

    async def close(self) -> None:
        """Close the underlying SDK client. Call this in FastAPI lifespan shutdown.

        The credential is the shared process-wide singleton — it is intentionally NOT
        closed here so other Azure clients (Key Vault) keep working after blob shutdown.
        """
        await self._client.close()


async def generate_evidence_sas_url(
    blob_client: "BlobStorageClient",
    blob_name: str,
    ttl_hours: int = 24,
) -> str:
    """Generate a user-delegation read SAS URL for an evidence ZIP in Azure Blob.

    Uses DefaultAzureCredential (Managed Identity) via get_user_delegation_key()
    to mint the SAS token — this is the ONLY correct pattern when the app does not
    have access to the storage account key (RESEARCH Pitfall 3, T-M8-15).

    Args:
        blob_client: The live BlobStorageClient instance (from app.state.blob_client).
        blob_name:   The blob path (e.g. "{tenant_id}/evidence/{run_id}/{job_id}.zip").
        ttl_hours:   How long the SAS URL remains valid (default 24 hours).

    Returns:
        A full HTTPS SAS URL with read-only permission and ttl_hours expiry.

    Security properties (T-M8-15):
        - BlobSasPermissions(read=True) — no write, delete, or list
        - ttl_hours default 24 — short-lived, minimises exposure window
        - HTTPS only (Azure enforces this for user-delegation SAS on modern accounts)
    """
    now = datetime.now(tz=timezone.utc)
    key_start = now - timedelta(minutes=5)   # small back-date to avoid clock skew
    key_expiry = now + timedelta(hours=ttl_hours)

    # Step 1: mint a user-delegation key via the storage service
    # (requires Storage Blob Data Delegator role on the account — Managed Identity)
    udk = await blob_client._client.get_user_delegation_key(
        key_start_time=key_start,
        key_expiry_time=key_expiry,
    )

    # Step 2: generate the SAS token using the user-delegation key (NOT account_key)
    account_name = blob_client._client.account_name
    sas_token = generate_blob_sas(
        account_name=account_name,
        container_name=blob_client._container,
        blob_name=blob_name,
        user_delegation_key=udk,
        permission=BlobSasPermissions(read=True),
        expiry=key_expiry,
        start=key_start,
    )

    return (
        f"https://{account_name}.blob.core.windows.net/"
        f"{blob_client._container}/{blob_name}?{sas_token}"
    )
