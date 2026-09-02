"""Tenant-scoped secret store with two interchangeable backends:

  - Azure Key Vault (prod) — when AZURE_KEY_VAULT_URL is configured.
  - Fernet-encrypted DB row (local dev / no-KV) — under SECRET_STORE_KEY.

The plaintext secret is NEVER logged and NEVER returned to clients by callers.
app_secrets is FORCE-RLS, so DB-backend calls run inside a tenant session.

This module owns the TENANT vault. Everything it stores is a per-tenant credential the
app must both read and write (BYOK model keys, connector/MCP secrets), so it addresses
AZURE_TENANT_VAULT_URL rather than the platform vault. The platform vault holds secrets
the app only reads and its identity has no write grant there — keeping the two apart is
what lets credential writes work without also permitting jwt-secret-key to be replaced.
AZURE_TENANT_VAULT_URL falls back to AZURE_KEY_VAULT_URL when unset (config/env.py), so
single-vault deployments are unaffected.

IMPORTANT: AZURE_KEY_VAULT_URL, AZURE_TENANT_VAULT_URL and SECRET_STORE_KEY are imported
as module-level names (not accessed via the config module) so tests can monkeypatch them
with `monkeypatch.setattr(ss, "AZURE_KEY_VAULT_URL", ...)` to force the DB backend.
"""
from __future__ import annotations

import logging
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import text

from config.env import (  # noqa: F401 — re-exported for monkeypatch
    AZURE_KEY_VAULT_URL,
    AZURE_TENANT_VAULT_URL,
    SECRET_STORE_KEY,
)
from shared.db import get_db_session_for_tenant
from shared.keyvault import load_secret, store_secret
from shared.keyvault import delete_secret as _kv_delete_secret

logger = logging.getLogger(__name__)


class SecretWriteError(RuntimeError):
    """A credential could not be persisted to the secret store.

    Raised instead of returning silently, because the caller's next step is to INSERT a
    row whose secret_ref points at the value we just failed to write. Swallowing this
    produced a provider row referencing a secret that never existed: the API reported
    success and every later run failed closed with "no model configured". The dominant
    cause is the app identity holding "Key Vault Secrets User" (read-only) on the vault
    it writes to — grant "Key Vault Secrets Officer" on the TENANT vault.
    """

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


def _tenant_vault() -> str:
    """The vault this module reads and writes. Read through globals() so the
    monkeypatch contract in the module docstring holds for this name too."""
    import shared.services.secret_store as _self
    return _self.AZURE_TENANT_VAULT_URL or _self.AZURE_KEY_VAULT_URL


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
        # store_secret never raises; it reports failure as False. Check it — a dropped
        # write here is a credential silently lost.
        if not await store_secret(ref, value, tenant_id=tenant_id, vault_url=_tenant_vault()):
            raise SecretWriteError(
                f"Key Vault write failed for ref={ref!r} (tenant={tenant_id}). "
                "Check the app identity holds 'Key Vault Secrets Officer' on the tenant vault."
            )
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
        return await load_secret(ref, tenant_id=tenant_id, vault_url=_tenant_vault())
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
        await _kv_delete_secret(ref, tenant_id=tenant_id, vault_url=_tenant_vault())
        return
    async with get_db_session_for_tenant(tenant_id) as s:
        await s.execute(
            text("DELETE FROM app_secrets WHERE tenant_id = :t AND ref = :r"),
            {"t": tenant_id, "r": ref},
        )
    logger.debug("secret deleted (db backend) tenant=%s ref=%s", tenant_id, ref)
