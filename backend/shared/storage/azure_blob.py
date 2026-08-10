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
