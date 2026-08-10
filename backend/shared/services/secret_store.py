"""Tenant-scoped secret store with two interchangeable backends:

  - Azure Key Vault (prod) — when AZURE_KEY_VAULT_URL is configured.
  - Fernet-encrypted DB row (local dev / no-KV) — under SECRET_STORE_KEY.

The plaintext secret is NEVER logged and NEVER returned to clients by callers.
app_secrets is FORCE-RLS, so DB-backend calls run inside a tenant session.

IMPORTANT: AZURE_KEY_VAULT_URL and SECRET_STORE_KEY are imported as module-level
names (not accessed via the config module) so tests can monkeypatch them with
`monkeypatch.setattr(ss, "AZURE_KEY_VAULT_URL", ...)` to force the DB backend.
"""
from __future__ import annotations

import logging
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import text

from config.env import AZURE_KEY_VAULT_URL, SECRET_STORE_KEY  # noqa: F401 — re-exported for monkeypatch
from shared.db import get_db_session_for_tenant
from shared.keyvault import load_secret, store_secret
from shared.keyvault import delete_secret as _kv_delete_secret

logger = logging.getLogger(__name__)

# Authoritative "explicitly disconnected" tombstone. Azure Key Vault rejects empty
# secret values, so disconnect overwrites the credential with this marker instead of
# deleting (which can fail when the KV identity lacks secrets/delete). The read paths
# (connector auth_adapter, connectors overlay) treat this value as "no credential".
DISCONNECTED_MARKER = "__disconnected__"

# Keep the module-level names importable as attributes (monkeypatch target).
# They are re-read on each call via globals() so monkeypatch works at test time.


def _use_kv() -> bool:
    """True when Azure Key Vault is configured — use KV backend."""
    import shared.services.secret_store as _self
    return bool(_self.AZURE_KEY_VAULT_URL)


def _fernet() -> Fernet:
    """Return a Fernet instance for the DB backend. Raises RuntimeError if the key is unset."""
    import shared.services.secret_store as _self
    key = _self.SECRET_STORE_KEY
    if not key:
        raise RuntimeError(
            "SECRET_STORE_KEY is not set — required for the DB secret-store backend "
            "when Azure Key Vault is not configured."
        )
    return Fernet(key.encode())


async def put_secret(tenant_id: str, ref: str, value: str) -> None:
    """Store a secret for a tenant under the given ref.

    KV backend: stores as '{tenant_id}-{ref}' in Azure Key Vault.
    DB backend: encrypts with Fernet(SECRET_STORE_KEY) and upserts into app_secrets.
    The plaintext value is never logged — only tenant/ref labels.
    """
    if _use_kv():
        await store_secret(ref, value, tenant_id=tenant_id)
        return
    ciphertext = _fernet().encrypt(value.encode()).decode()
    async with get_db_session_for_tenant(tenant_id) as s:
        await s.execute(
            text(
                "INSERT INTO app_secrets (tenant_id, ref, ciphertext) "
                "VALUES (:t, :r, :c) "
                "ON CONFLICT (tenant_id, ref) DO UPDATE SET ciphertext = EXCLUDED.ciphertext, "
                "updated_at = now()"
            ),
            {"t": tenant_id, "r": ref, "c": ciphertext},
        )
    logger.debug("secret stored (db backend) tenant=%s ref=%s", tenant_id, ref)


async def get_secret(tenant_id: str, ref: str) -> Optional[str]:
    """Retrieve a secret. Returns plaintext on success, None if absent or decryption fails.

    The plaintext is returned to the caller for internal use only; never log the return value.
    """
    if _use_kv():
        return await load_secret(ref, tenant_id=tenant_id)
    async with get_db_session_for_tenant(tenant_id) as s:
        ciphertext = (
            await s.execute(
                text("SELECT ciphertext FROM app_secrets WHERE tenant_id = :t AND ref = :r"),
                {"t": tenant_id, "r": ref},
            )
        ).scalar()
    if ciphertext is None:
        return None
    try:
        return _fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken:
        logger.error("secret decrypt failed tenant=%s ref=%s — wrong SECRET_STORE_KEY?", tenant_id, ref)
        return None


async def delete_secret(tenant_id: str, ref: str) -> None:
    """Delete a secret. No-op if not found (idempotent).

    KV backend: begins Key Vault soft-delete for '{tenant_id}-{ref}'.
    DB backend: removes the row from app_secrets under the tenant session.
    """
    if _use_kv():
        await _kv_delete_secret(ref, tenant_id=tenant_id)
        return
    async with get_db_session_for_tenant(tenant_id) as s:
        await s.execute(
            text("DELETE FROM app_secrets WHERE tenant_id = :t AND ref = :r"),
            {"t": tenant_id, "r": ref},
        )
    logger.debug("secret deleted (db backend) tenant=%s ref=%s", tenant_id, ref)
