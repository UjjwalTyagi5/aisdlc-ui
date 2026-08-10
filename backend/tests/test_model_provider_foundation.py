"""Phase 1 — Model Provider foundation tests (catalog perm, RLS tables, secret store)."""
import uuid

import pytest
from sqlalchemy import text

from shared.authz.permissions import ALL_PERMISSIONS, has_permission


def test_model_manage_in_catalog():
    assert "model:manage" in ALL_PERMISSIONS


def test_admin_wildcard_grants_model_manage():
    assert has_permission(["admin:*"], "model:manage") is True
    assert has_permission(["run:create"], "model:manage") is False


@pytest.fixture(autouse=True)
async def _dispose_shared_engine():
    yield
    from shared.db import engine
    await engine.dispose()


@pytest.mark.asyncio
async def test_model_providers_rls_isolates_tenants():
    """A provider row written under tenant A is invisible under tenant B's session.
    SKIPS under a BYPASSRLS/superuser connection (run via the restricted sdlc_app DSN)."""
    from shared.db import get_db_session_for_tenant

    tenant_a = str(uuid.uuid4())
    tenant_b = str(uuid.uuid4())
    pid = str(uuid.uuid4())

    async with get_db_session_for_tenant(tenant_a) as s:
        # If the connection bypasses RLS (superuser), this isolation test is meaningless.
        bypass = (await s.execute(text("SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user"))).scalar()
        if bypass:
            pytest.skip("connection bypasses RLS (superuser) — run via restricted sdlc_app DSN")
        await s.execute(
            text("INSERT INTO model_providers (id, tenant_id, provider, display_name, secret_ref, status, created_by) "
                 "VALUES (:id, :t, 'anthropic', 'A key', :ref, 'unverified', 'tester')"),
            {"id": pid, "t": tenant_a, "ref": f"model-{pid}"},
        )

    async with get_db_session_for_tenant(tenant_b) as s:
        seen = (await s.execute(text("SELECT COUNT(*) FROM model_providers WHERE id = :id"), {"id": pid})).scalar()
    assert seen == 0  # tenant B cannot see tenant A's provider


@pytest.mark.asyncio
async def test_secret_store_db_backend_roundtrip(monkeypatch):
    """With no Key Vault configured, secret_store uses the Fernet-encrypted DB
    backend: put → get returns plaintext; the stored ciphertext is NOT the plaintext;
    delete removes it. SKIPS under a BYPASSRLS connection (app_secrets is RLS)."""
    from cryptography.fernet import Fernet
    import shared.services.secret_store as ss

    # Force DB backend + a known key regardless of local env.
    monkeypatch.setattr(ss, "AZURE_KEY_VAULT_URL", "", raising=False)
    monkeypatch.setattr(ss, "SECRET_STORE_KEY", Fernet.generate_key().decode(), raising=False)

    from shared.db import get_db_session_for_tenant
    tenant = str(uuid.uuid4())
    async with get_db_session_for_tenant(tenant) as s:
        bypass = (await s.execute(text("SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user"))).scalar()
    if bypass:
        pytest.skip("connection bypasses RLS — run via restricted sdlc_app DSN")

    await ss.put_secret(tenant, "model-x", "sk-secret-123")
    assert await ss.get_secret(tenant, "model-x") == "sk-secret-123"

    async with get_db_session_for_tenant(tenant) as s:
        raw = (await s.execute(text("SELECT ciphertext FROM app_secrets WHERE tenant_id = :t AND ref = 'model-x'"), {"t": tenant})).scalar()
    assert raw is not None and "sk-secret-123" not in raw  # stored encrypted

    await ss.delete_secret(tenant, "model-x")
    assert await ss.get_secret(tenant, "model-x") is None


def test_model_catalog_providers_and_validation():
    from shared.services.model_catalog import (
        PROVIDERS,
        is_valid_model,
        list_providers,
        models_for_provider,
    )

    names = {p["provider"] for p in list_providers()}
    assert {"anthropic", "openai", "google"} <= names
    anthropic_models = {m["model_id"] for m in models_for_provider("anthropic")}
    assert "claude-sonnet-4-6" in anthropic_models
    assert is_valid_model("anthropic", "claude-sonnet-4-6") is True
    assert is_valid_model("anthropic", "gpt-4o") is False
    assert is_valid_model("nope", "x") is False
    assert PROVIDERS
