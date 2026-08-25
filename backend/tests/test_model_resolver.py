"""Phase 3 — model_resolver tests (live Postgres; secret_store via DB backend)."""
import uuid
import pytest


@pytest.fixture(autouse=True)
async def _dispose_shared_engine():
    yield
    from shared.db import engine
    await engine.dispose()


async def _seed_valid_provider(tenant, models, default_model):
    """Create a provider, force status='valid', set one offering as default."""
    from shared.services import model_config as mc
    from sqlalchemy import text
    from shared.db import get_db_session_for_tenant
    created = await mc.create_provider(
        tenant, provider="anthropic", display_name="Acme",
        api_key="sk-byok-xyz", enabled_models=models, created_by="admin1",
    )
    async with get_db_session_for_tenant(tenant) as s:
        await s.execute(text("UPDATE model_providers SET status='valid' WHERE id=:i AND tenant_id=:t"),
                        {"i": created["id"], "t": tenant})
        await s.execute(
            text("UPDATE model_offerings SET is_default=true WHERE provider_id=:p AND model_id=:m AND tenant_id=:t"),
            {"p": created["id"], "m": default_model, "t": tenant})
    return created


@pytest.mark.asyncio
async def test_resolves_org_default_when_no_request():
    from shared.services.model_resolver import resolve_model_for_run
    tenant = str(uuid.uuid4())
    await _seed_valid_provider(tenant, ["claude-sonnet-4-6", "claude-opus-4-8"], "claude-opus-4-8")

    resolved = await resolve_model_for_run(tenant, None)

    assert resolved.provider == "anthropic"
    assert resolved.model == "claude-opus-4-8"      # the org default
    assert resolved.api_key  # BYOK key round-tripped from secret_store (value not asserted literally to keep it out of CI logs)
    assert resolved.litellm_provider == "anthropic"
    assert resolved.alias.startswith("tenant:")


@pytest.mark.asyncio
async def test_resolves_requested_model_when_enabled():
    from shared.services.model_resolver import resolve_model_for_run
    tenant = str(uuid.uuid4())
    await _seed_valid_provider(tenant, ["claude-sonnet-4-6", "claude-opus-4-8"], "claude-opus-4-8")

    resolved = await resolve_model_for_run(tenant, "claude-sonnet-4-6")

    assert resolved.model == "claude-sonnet-4-6"   # honoured the per-run request, not the default


@pytest.mark.asyncio
async def test_rejects_requested_model_not_enabled():
    from shared.services.model_resolver import resolve_model_for_run, ModelNotEnabledError
    tenant = str(uuid.uuid4())
    await _seed_valid_provider(tenant, ["claude-sonnet-4-6"], "claude-sonnet-4-6")

    with pytest.raises(ModelNotEnabledError):
        await resolve_model_for_run(tenant, "claude-opus-4-8")   # enabled in catalog, but not for this org


@pytest.mark.asyncio
async def test_fails_closed_when_no_provider():
    from shared.services.model_resolver import resolve_model_for_run, NoModelConfiguredError
    tenant = str(uuid.uuid4())   # nothing configured
    with pytest.raises(NoModelConfiguredError):
        await resolve_model_for_run(tenant, None)


@pytest.mark.asyncio
async def test_fails_closed_when_provider_unverified():
    """A provider that exists but is NOT status='valid' must not be resolvable."""
    from shared.services import model_config as mc
    from shared.services.model_resolver import resolve_model_for_run, NoModelConfiguredError
    tenant = str(uuid.uuid4())
    await mc.create_provider(  # left status='unverified'
        tenant, provider="anthropic", display_name="Unverified",
        api_key="k", enabled_models=["claude-sonnet-4-6"], created_by="a")
    with pytest.raises(NoModelConfiguredError):
        await resolve_model_for_run(tenant, None)


@pytest.mark.asyncio
async def test_key_never_appears_in_logs(caplog):
    import logging
    from shared.services.model_resolver import resolve_model_for_run
    tenant = str(uuid.uuid4())
    await _seed_valid_provider(tenant, ["claude-sonnet-4-6"], "claude-sonnet-4-6")
    with caplog.at_level(logging.DEBUG):
        await resolve_model_for_run(tenant, None)
    assert "sk-byok-xyz" not in caplog.text


@pytest.mark.asyncio
async def test_offering_id_resolves_exact_connection_even_with_duplicate_model():
    """Two connections (keys) expose the SAME model_id — offering_id must pick the
    exact one, unambiguously (the Phase-2 fix for silent first-match)."""
    from shared.services import model_config as mc
    from shared.services.model_resolver import resolve_model_for_run
    from sqlalchemy import text
    from shared.db import get_db_session_for_tenant
    tenant = str(uuid.uuid4())
    await mc.create_provider(tenant, provider="anthropic", display_name="Prod key",
                             api_key="sk-prod", enabled_models=["claude-sonnet-4-6"], created_by="a")
    p2 = await mc.create_provider(tenant, provider="anthropic", display_name="Sandbox key",
                                  api_key="sk-sandbox", enabled_models=["claude-sonnet-4-6"], created_by="a")
    async with get_db_session_for_tenant(tenant) as s:
        await s.execute(text("UPDATE model_providers SET status='valid' WHERE tenant_id=:t"), {"t": tenant})
    off2 = p2["offerings"][0]["id"]

    resolved = await resolve_model_for_run(tenant, "claude-sonnet-4-6", offering_id=off2)

    assert resolved.offering_id == off2
    assert resolved.display_name == "Sandbox key"
    assert p2["id"] in resolved.alias  # the Sandbox connection's key was chosen, not Prod's


@pytest.mark.asyncio
async def test_duplicate_display_name_rejected():
    """Connection names are unique per tenant (case-insensitive) so a chosen model
    is always identifiable."""
    from shared.services import model_config as mc
    tenant = str(uuid.uuid4())
    await mc.create_provider(tenant, provider="anthropic", display_name="Anthropic",
                             api_key="k", enabled_models=["claude-sonnet-4-6"], created_by="a")
    with pytest.raises(mc.DuplicateProviderNameError):
        await mc.create_provider(tenant, provider="anthropic", display_name="anthropic",
                                 api_key="k2", enabled_models=["claude-opus-4-8"], created_by="a")


@pytest.mark.asyncio
async def test_resolver_respects_project_grant_scope():
    from shared.services import model_config as mc
    from shared.services import model_grants as mg
    from shared.services.model_resolver import resolve_model_for_run, ModelNotEnabledError
    from shared.db import get_db_session_for_tenant
    from sqlalchemy import text
    import uuid as _uuid

    tenant = str(_uuid.uuid4())
    ws_id = str(_uuid.uuid4())
    proj_id = str(_uuid.uuid4())
    async with get_db_session_for_tenant(tenant) as s:
        await s.execute(
            text("INSERT INTO organizations (id, slug, display_name, created_at, updated_at) "
                 "VALUES (:id, :slug, :dn, now(), now()) ON CONFLICT (id) DO NOTHING"),
            {"id": tenant, "slug": f"org-{tenant[:8]}", "dn": "Org"},
        )
        await s.execute(
            text("INSERT INTO workspaces (id, organization_id, slug, display_name, created_at, updated_at) "
                 "VALUES (:id, :org_id, :slug, :dn, now(), now())"),
            {"id": ws_id, "org_id": tenant, "slug": f"ws-{ws_id[:8]}", "dn": "Unit A"},
        )
        await s.execute(
            text("INSERT INTO projects (id, workspace_id, tenant_id, display_name, created_at, updated_at) "
                 "VALUES (:id, :ws_id, :t, :dn, now(), now())"),
            {"id": proj_id, "ws_id": ws_id, "t": tenant, "dn": "Proj"},
        )

    created = await mc.create_provider(
        tenant, provider="anthropic", display_name="Acme", api_key="sk-x",
        enabled_models=["claude-sonnet-4-6", "claude-opus-4-8"], created_by="admin1",
    )
    async with get_db_session_for_tenant(tenant) as s:
        await s.execute(text("UPDATE model_providers SET status='valid' WHERE id=:i"), {"i": created["id"]})

    # get_bu_allowed now also requires the PROVIDER to be granted to this BU
    # (integration_grants(kind='model_provider')), not just the model-level
    # org_model_grants entry below — see model_grants.py's coupling fix.
    from tests.test_model_grants import _grant_provider
    await _grant_provider(tenant, ws_id)

    # Grant only claude-sonnet-4-6 to this project's BU — claude-opus-4-8 is onboarded
    # but never granted, so it must become unresolvable for this project.
    await mg.set_org_grants(
        tenant,
        [{"provider": "anthropic", "model_id": "claude-sonnet-4-6", "credential_id": created["id"], "visibility": "global", "business_unit_ids": []}],
        created_by="admin1",
    )

    resolved = await resolve_model_for_run(tenant, "claude-sonnet-4-6", project_id=proj_id)
    assert resolved.model == "claude-sonnet-4-6"

    with pytest.raises(ModelNotEnabledError):
        await resolve_model_for_run(tenant, "claude-opus-4-8", project_id=proj_id)


@pytest.mark.asyncio
async def test_resolver_stays_open_with_zero_grants_configured():
    """Backward-compat: a tenant with no org_model_grants rows resolves exactly as today."""
    from shared.services import model_config as mc
    from shared.services.model_resolver import resolve_model_for_run
    from shared.db import get_db_session_for_tenant
    from sqlalchemy import text
    import uuid as _uuid

    tenant = str(_uuid.uuid4())
    created = await mc.create_provider(
        tenant, provider="anthropic", display_name="Acme", api_key="sk-x",
        enabled_models=["claude-sonnet-4-6"], created_by="admin1",
    )
    async with get_db_session_for_tenant(tenant) as s:
        await s.execute(text("UPDATE model_providers SET status='valid' WHERE id=:i"), {"i": created["id"]})

    # No grants at all, no project_id passed — must resolve exactly as before this feature.
    resolved = await resolve_model_for_run(tenant, "claude-sonnet-4-6")
    assert resolved.model == "claude-sonnet-4-6"
