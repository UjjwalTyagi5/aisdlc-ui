"""Azure Key Vault secret loader for agentic_app.

load_secret() is called at FastAPI startup via lifespan. Non-blocking: returns
None on failure so local dev without Azure continues normally.

Two vaults, split by what the app is allowed to DO with the contents:
  - AZURE_KEY_VAULT_URL        platform secrets, read-only for the app (the default)
  - AZURE_TENANT_VAULT_URL     per-tenant credentials the app also writes

Every function takes an optional `vault_url` and defaults to the platform vault, so
existing callers are unchanged. shared/services/secret_store.py is the only module
that passes the tenant vault — see its docstring.
"""
import asyncio
import logging
import re
from typing import Optional

from azure.core.exceptions import ResourceNotFoundError
from azure.keyvault.secrets.aio import SecretClient

from config.env import AZURE_KEY_VAULT_URL, AZURE_TENANT_VAULT_URL  # noqa: F401 — re-exported for monkeypatch
from shared.azure_credential import get_azure_credential

logger = logging.getLogger(__name__)

_KEYVAULT_TIMEOUT_SECONDS = 15

# Key Vault permits alphanumerics and dashes only, and rejects anything else with a
# 400 at request time. Checked here rather than trusted, because the resolved name is
# built from a caller-supplied tenant id.
_LEGAL_SECRET_NAME = re.compile(r"[0-9a-zA-Z-]+")


def _vault(vault_url: Optional[str]) -> str:
    """Resolve the vault to address, defaulting to the platform vault.

    Read through globals() so monkeypatching the module-level names in tests takes
    effect (same contract secret_store.py documents for its own constants).
    """
    if vault_url:
        return vault_url
    import shared.keyvault as _self
    return _self.AZURE_KEY_VAULT_URL


async def load_secret(secret_name: str, tenant_id: str | None = None, *,
                      vault_url: Optional[str] = None) -> Optional[str]:
    """Read a secret from Key Vault. Returns None on failure. Non-blocking — safe to call at startup.

    When tenant_id is provided the resolved secret name is '{tenant_id}-{secret_name}'.
    When tenant_id is None the secret is loaded by exact name (platform/global secrets).
    Raises ValueError in connector auth_adapter() paths when tenant_id is required
    but not provided (enforced by caller, not here).
    """
    resolved_name = f"{tenant_id}-{secret_name}" if tenant_id else secret_name

    # A name Key Vault cannot hold cannot be IN Key Vault, so asking is a round trip
    # whose only possible answer is 400. Azure raises HttpResponseError for that —
    # NOT the ResourceNotFoundError the "not configured" path below handles quietly —
    # so every such call logged a WARNING for a secret that could never have existed.
    #
    # The caller that made this show up: connector health probes resolve credentials
    # under the sentinel tenant `__health_probe__`, which has underscores, so a
    # deployment pointed at a real vault printed a warning per connector per probe
    # cycle for a lookup that was never going to succeed.
    if not _LEGAL_SECRET_NAME.fullmatch(resolved_name):
        logger.debug(
            "Secret name %r is not a legal Key Vault name — skipping the read",
            resolved_name,
        )
        return None

    _url = _vault(vault_url)
    if not _url:
        logger.debug("No Key Vault URL configured — skipping Key Vault read")
        return None

    credential = get_azure_credential()  # shared, do not close
    try:
        async with SecretClient(vault_url=_url, credential=credential) as client:
            secret = await asyncio.wait_for(
                client.get_secret(resolved_name),
                timeout=_KEYVAULT_TIMEOUT_SECONDS,
            )
            logger.debug("Loaded secret '%s' from Key Vault", resolved_name)
            return secret.value
    except asyncio.TimeoutError:
        logger.warning(
            "Key Vault read timed out after %ds for secret '%s'",
            _KEYVAULT_TIMEOUT_SECONDS,
            resolved_name,
        )
        return None
    except ResourceNotFoundError:
        # Expected for any secret not yet configured (e.g. a connector nobody has
        # connected). Not an error — DEBUG, not WARNING.
        logger.debug("Key Vault secret '%s' not set (expected until configured)", resolved_name)
        return None
    except Exception as exc:
        logger.warning("Key Vault secret '%s' unavailable: %s", resolved_name, type(exc).__name__)
        return None


def load_secret_sync(secret_name: str, *, vault_url: Optional[str] = None) -> Optional[str]:
    """Synchronous Key Vault read for import-time resolution.

    Used by the rare at-import secret reads where async is unavailable: the DB engine
    (shared/db.py), the LangGraph checkpointer (config/checkpoint.py), and Alembic
    (migrations/env.py). Returns None on any failure so callers fall back to env.
    Runtime request code should use the async load_secret() instead.
    """
    _url = _vault(vault_url)
    if not _url:
        return None
    try:
        import shared.azure_credential  # noqa: F401 — quiets Azure SDK logging
        from azure.identity import DefaultAzureCredential
        from azure.keyvault.secrets import SecretClient as SyncSecretClient

        credential = DefaultAzureCredential()
        try:
            client = SyncSecretClient(vault_url=_url, credential=credential)
            try:
                return client.get_secret(secret_name).value
            finally:
                client.close()
        finally:
            credential.close()
    except ResourceNotFoundError:
        logger.debug("Key Vault secret '%s' not set (expected until configured)", secret_name)
        return None
    except Exception as exc:  # noqa: BLE001 — fall back to env on any failure
        logger.warning(
            "Sync Key Vault read failed for '%s' (%s)", secret_name, type(exc).__name__
        )
        return None


async def delete_secret(secret_name: str, tenant_id: str | None = None, *,
                        vault_url: Optional[str] = None) -> bool:
    """Begin-delete a Key Vault secret. Returns True on success, False if KV is
    unconfigured or the call fails (caller treats absence as already-deleted).

    When tenant_id is provided the resolved secret name is '{tenant_id}-{secret_name}'.
    Non-blocking — returns False (never raises) so callers can treat absence as already-deleted.
    """
    resolved_name = f"{tenant_id}-{secret_name}" if tenant_id else secret_name

    # An illegal name on a WRITE is a caller bug, not a routine miss — the value would
    # be silently lost. WARNING, not debug, and False rather than a raise, matching how
    # every other failure in this function is reported.
    if not _LEGAL_SECRET_NAME.fullmatch(resolved_name):
        logger.warning(
            "Refusing Key Vault write: %r is not a legal secret name "
            "(alphanumerics and dashes only)",
            resolved_name,
        )
        return False

    _url = _vault(vault_url)
    if not _url:
        logger.debug("No Key Vault URL configured — skipping Key Vault delete for '%s'", resolved_name)
        return False

    credential = get_azure_credential()  # shared, do not close
    try:
        async with SecretClient(vault_url=_url, credential=credential) as client:
            # NOTE: the ASYNC SecretClient exposes `delete_secret` (a coroutine), NOT the
            # sync client's `begin_delete_secret` poller. Calling the latter raised
            # AttributeError that the broad except below swallowed — so every secret
            # delete silently no-op'd and credentials survived in Key Vault.
            await asyncio.wait_for(
                client.delete_secret(resolved_name),
                timeout=_KEYVAULT_TIMEOUT_SECONDS,
            )
            logger.debug("Deleted secret '%s' from Key Vault", resolved_name)
            return True
    except asyncio.TimeoutError:
        logger.warning(
            "Key Vault delete timed out after %ds for secret '%s'",
            _KEYVAULT_TIMEOUT_SECONDS,
            resolved_name,
        )
        return False
    except Exception as exc:
        # Log the exception TYPE (never the value) so the cause is diagnosable —
        # the dominant cause is the KV identity lacking the secrets/delete action
        # (grant "Key Vault Secrets Officer"). Caller treats absence as deleted.
        logger.warning(
            "KV delete failed for secret '%s' (tenant=%s): %s — treating as absent",
            resolved_name,
            tenant_id,
            type(exc).__name__,
        )
        return False


async def store_secret(secret_name: str, value: str, tenant_id: str | None = None, *,
                       vault_url: Optional[str] = None) -> bool:
    """Write a secret to Key Vault. Returns True on success, False on failure.

    When tenant_id is provided the resolved secret name is '{tenant_id}-{secret_name}'.
    Non-blocking — returns False (never raises) so callers can decide whether absence is fatal.
    Used by scim/admin.py set-credential CLI (Wave A) and future connector OAuth callbacks.
    """
    resolved_name = f"{tenant_id}-{secret_name}" if tenant_id else secret_name

    # An illegal name on a WRITE is a caller bug, not a routine miss — the value would
    # be silently lost. WARNING, not debug, and False rather than a raise, matching how
    # every other failure in this function is reported.
    if not _LEGAL_SECRET_NAME.fullmatch(resolved_name):
        logger.warning(
            "Refusing Key Vault write: %r is not a legal secret name "
            "(alphanumerics and dashes only)",
            resolved_name,
        )
        return False

    _url = _vault(vault_url)
    if not _url:
        logger.debug("No Key Vault URL configured — skipping Key Vault write for '%s'", resolved_name)
        return False

    credential = get_azure_credential()  # shared, do not close
    try:
        async with SecretClient(vault_url=_url, credential=credential) as client:
            await asyncio.wait_for(
                client.set_secret(resolved_name, value),
                timeout=_KEYVAULT_TIMEOUT_SECONDS,
            )
            logger.debug("Stored secret '%s' in Key Vault", resolved_name)
            return True
    except asyncio.TimeoutError:
        logger.warning(
            "Key Vault write timed out after %ds for secret '%s'",
            _KEYVAULT_TIMEOUT_SECONDS,
            resolved_name,
        )
        return False
    except Exception as exc:
        logger.warning("Key Vault write failed for secret '%s': %s", resolved_name, exc)
        return False
