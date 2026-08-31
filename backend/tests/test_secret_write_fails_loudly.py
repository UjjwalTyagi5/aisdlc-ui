"""A credential that cannot be stored must abort the operation, not be swallowed.

shared/keyvault.store_secret reports failure by RETURNING FALSE (it never raises, by
design, so callers decide whether absence is fatal). secret_store.put_secret used to
discard that bool. The result in production: the app identity held "Key Vault Secrets
User" (read-only), Azure returned 403 on every write, and create_provider went on to
INSERT a model_providers row whose secret_ref pointed at a secret that was never
written. The API reported 201; every later run failed closed with "no model configured".

These tests pin the two halves of the fix: put_secret raises, and no orphan row survives.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from shared.services import secret_store as ss


@pytest.fixture
def kv_backend(monkeypatch):
    """Force the Key Vault backend (the DB/Fernet backend has no such failure mode)."""
    monkeypatch.setattr(ss, "AZURE_KEY_VAULT_URL", "https://platform.vault.azure.net/")
    monkeypatch.setattr(ss, "AZURE_TENANT_VAULT_URL", "https://tenant.vault.azure.net/")


async def test_put_secret_raises_when_the_vault_write_fails(kv_backend, monkeypatch):
    """The core regression: False from store_secret must become an exception."""
    monkeypatch.setattr(ss, "store_secret", AsyncMock(return_value=False))

    with pytest.raises(ss.SecretWriteError) as exc:
        await ss.put_secret("tenant-1", "model-abc", "sk-ant-secret")

    # The message must point the operator at the actual cause without leaking the value.
    assert "sk-ant-secret" not in str(exc.value)
    assert "Secrets Officer" in str(exc.value)


async def test_put_secret_succeeds_when_the_vault_accepts_it(kv_backend, monkeypatch):
    store = AsyncMock(return_value=True)
    monkeypatch.setattr(ss, "store_secret", store)

    await ss.put_secret("tenant-1", "model-abc", "sk-ant-secret")

    # Routed at the TENANT vault, not the platform vault.
    assert store.await_args.kwargs["vault_url"] == "https://tenant.vault.azure.net/"


async def test_create_provider_leaves_no_orphan_row(kv_backend, monkeypatch):
    """A failed vault write must abort BEFORE the model_providers INSERT.

    Asserted by proving the DB session is never opened — create_provider calls
    put_secret first, so the raise happens before any row can be written.
    """
    from shared.services import model_config as mc

    monkeypatch.setattr(ss, "store_secret", AsyncMock(return_value=False))

    opened = []

    def _tripwire(*a, **kw):
        opened.append(a)
        raise AssertionError(
            "a DB session was opened after the credential write failed — "
            "that is the orphan-row bug"
        )

    # _name_exists runs first on its own session; let that one through, then trip.
    monkeypatch.setattr(mc, "_name_exists", AsyncMock(return_value=False))

    with pytest.raises(ss.SecretWriteError):
        await mc.create_provider(
            "tenant-1",
            provider="anthropic",
            display_name="Anthropic",
            api_key="sk-ant-secret",
            models=[{"model_id": "claude-opus-4-5"}],
            created_by="user-1",
        )

    assert opened == []


async def test_a_keyless_provider_is_unaffected(kv_backend, monkeypatch):
    """Providers onboarded without a key never touch the secret store."""
    store = AsyncMock(return_value=False)
    monkeypatch.setattr(ss, "store_secret", store)

    # No api_key => no put_secret => the failing vault is irrelevant.
    from shared.services import model_config as mc

    assert mc._secret_ref("abc") == "model-abc"
    store.assert_not_awaited()
