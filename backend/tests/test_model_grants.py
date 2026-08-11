"""backend/tests/test_model_grants.py — org/BU/project grant cascade (live Postgres)."""
import uuid

import pytest
from sqlalchemy import text


@pytest.fixture(autouse=True)
async def _dispose_shared_engine():
    yield
    from shared.db import engine
    await engine.dispose()


async def _seed_org_workspace_project(tenant_id: str, ws_name: str = "Retail Banking"):
    """Insert a minimal organizations/workspaces/projects row-set for FK targets.
    Mirrors the pattern in tests/development/test_pr_persistence.py."""
    from shared.db import get_db_session_for_tenant

    ws_id = uuid.uuid4()
    proj_id = uuid.uuid4()
    async with get_db_session_for_tenant(tenant_id) as s:
        await s.execute(
            text(
                "INSERT INTO organizations (id, slug, display_name, created_at, updated_at) "
                "VALUES (:id, :slug, :dn, now(), now()) ON CONFLICT (id) DO NOTHING"
            ),
            {"id": tenant_id, "slug": f"org-{tenant_id[:8]}", "dn": "Test Org"},
        )
        await s.execute(
            text(
                "INSERT INTO workspaces (id, organization_id, slug, display_name, created_at, updated_at) "
                "VALUES (:id, :org_id, :slug, :dn, now(), now())"
            ),
            {"id": str(ws_id), "org_id": tenant_id, "slug": f"ws-{str(ws_id)[:8]}", "dn": ws_name},
        )
        await s.execute(
            text(
                "INSERT INTO projects (id, workspace_id, tenant_id, display_name, created_at, updated_at) "
                "VALUES (:id, :ws_id, :t, :dn, now(), now())"
            ),
            {"id": str(proj_id), "ws_id": str(ws_id), "t": tenant_id, "dn": "Mobile App"},
        )
    return str(ws_id), str(proj_id)


async def _seed_provider(tenant_id: str, models: list[str], workspace_id: str | None = None):
    from shared.services import model_config as mc
    return await mc.create_provider(
        tenant_id, provider="anthropic", display_name=f"conn-{uuid.uuid4().hex[:8]}",
        api_key="sk-byok-xyz", enabled_models=models, created_by="admin1",
        workspace_id=workspace_id,
    )


@pytest.mark.asyncio
async def test_global_grant_reaches_every_bu():
    from shared.services import model_grants as mg

    tenant = str(uuid.uuid4())
    ws_a, _ = await _seed_org_workspace_project(tenant, "Unit A")
    provider = await _seed_provider(tenant, ["claude-sonnet-4-6"])
    offering_id = provider["offerings"][0]["id"]

    await mg.set_org_grants(
        tenant,
        [{"provider": "anthropic", "model_id": "claude-sonnet-4-6", "credential_id": offering_id and provider["id"], "visibility": "global", "business_unit_ids": []}],
        created_by="admin1",
    )

    allowed = await mg.get_bu_allowed(tenant, ws_a)
    assert any(e["model_id"] == "claude-sonnet-4-6" for e in allowed)


@pytest.mark.asyncio
async def test_specific_grant_reaches_only_named_bu():
    from shared.services import model_grants as mg

    tenant = str(uuid.uuid4())
    ws_a, _ = await _seed_org_workspace_project(tenant, "Unit A")
    ws_b, _ = await _seed_org_workspace_project(tenant, "Unit B")
    provider = await _seed_provider(tenant, ["claude-opus-4-8"])

    await mg.set_org_grants(
        tenant,
        [{"provider": "anthropic", "model_id": "claude-opus-4-8", "credential_id": provider["id"], "visibility": "specific", "business_unit_ids": [ws_a]}],
        created_by="admin1",
    )

    allowed_a = await mg.get_bu_allowed(tenant, ws_a)
    allowed_b = await mg.get_bu_allowed(tenant, ws_b)
    assert any(e["model_id"] == "claude-opus-4-8" for e in allowed_a)
    assert not any(e["model_id"] == "claude-opus-4-8" for e in allowed_b)


@pytest.mark.asyncio
async def test_project_selection_rejects_out_of_grant_entry():
    from shared.services import model_grants as mg

    tenant = str(uuid.uuid4())
    ws_a, proj_a = await _seed_org_workspace_project(tenant, "Unit A")
    provider = await _seed_provider(tenant, ["claude-sonnet-4-6"])
    # NOTE: no grant created at all — the BU's allowed set is empty.

    with pytest.raises(mg.NotAllowedForUnitError):
        await mg.set_project_selection(
            tenant, proj_a,
            selected=[{"provider": "anthropic", "model_id": "claude-sonnet-4-6", "credential_id": provider["id"]}],
            default_key=None,
        )


@pytest.mark.asyncio
async def test_project_using_defaults_inherits_bu_set_live():
    from shared.services import model_grants as mg

    tenant = str(uuid.uuid4())
    ws_a, proj_a = await _seed_org_workspace_project(tenant, "Unit A")
    provider = await _seed_provider(tenant, ["claude-sonnet-4-6"])

    await mg.set_org_grants(
        tenant,
        [{"provider": "anthropic", "model_id": "claude-sonnet-4-6", "credential_id": provider["id"], "visibility": "global", "business_unit_ids": []}],
        created_by="admin1",
    )

    selection = await mg.get_project_selection(tenant, proj_a)
    assert selection["usingDefaults"] is True
    assert any(e["model_id"] == "claude-sonnet-4-6" for e in selection["inherited"])
    assert selection["selected"] == selection["inherited"]

    # Widen the BU's grant with a second model — the project's inherited view must move too.
    await mg.set_org_grants(
        tenant,
        [
            {"provider": "anthropic", "model_id": "claude-sonnet-4-6", "credential_id": provider["id"], "visibility": "global", "business_unit_ids": []},
            {"provider": "anthropic", "model_id": "claude-haiku-4-5-20251001", "credential_id": provider["id"], "visibility": "global", "business_unit_ids": []},
        ],
        created_by="admin1",
    )
    selection2 = await mg.get_project_selection(tenant, proj_a)
    assert len(selection2["inherited"]) == 2


@pytest.mark.asyncio
async def test_effective_project_offerings_none_when_no_grants_configured():
    from shared.services import model_grants as mg

    tenant = str(uuid.uuid4())
    _, proj_a = await _seed_org_workspace_project(tenant, "Unit A")
    # No org_model_grants rows for this tenant at all.

    result = await mg.effective_project_offerings(tenant, proj_a)
    assert result is None


@pytest.mark.asyncio
async def test_effective_project_offerings_scoped_once_a_grant_exists():
    from shared.services import model_grants as mg

    tenant = str(uuid.uuid4())
    ws_a, proj_a = await _seed_org_workspace_project(tenant, "Unit A")
    provider = await _seed_provider(tenant, ["claude-sonnet-4-6", "claude-opus-4-8"])
    offering_ids = {o["model_id"]: o["id"] for o in provider["offerings"]}

    await mg.set_org_grants(
        tenant,
        [{"provider": "anthropic", "model_id": "claude-sonnet-4-6", "credential_id": provider["id"], "visibility": "global", "business_unit_ids": []}],
        created_by="admin1",
    )

    result = await mg.effective_project_offerings(tenant, proj_a)
    assert result == {offering_ids["claude-sonnet-4-6"]}


@pytest.mark.asyncio
async def test_grant_matrix_one_row_per_model_credential_pair():
    from shared.services import model_grants as mg

    tenant = str(uuid.uuid4())
    await _seed_org_workspace_project(tenant, "Unit A")
    provider = await _seed_provider(tenant, ["claude-sonnet-4-6"])

    await mg.set_org_grants(
        tenant,
        [{"provider": "anthropic", "model_id": "claude-sonnet-4-6", "credential_id": provider["id"], "visibility": "global", "business_unit_ids": []}],
        created_by="admin1",
    )

    matrix = await mg.get_grant_matrix(tenant)
    rows = [r for r in matrix["rows"] if r["model_id"] == "claude-sonnet-4-6"]
    assert len(rows) == 1
    assert rows[0]["granted"] is True
    assert "anthropic" in matrix["centrallyKeyedProviders"]


# ---------------------------------------------------------------------------
# Final-review fix round: I2 — set_bu_grants must not 500 when a model already
# has a global grant, and must reject an unknown credential_id cleanly.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_set_bu_grants_noop_when_already_globally_granted():
    """I2(a): granting a BU a (provider, model_id, credential_id) that already has a
    `global` grant must succeed as a no-op — it's already allowed everywhere. Before the
    fix, `existing_keys` only looked at `specific` rows, so this fell through to an INSERT
    that violated uq_org_grant_cred and raised an uncaught IntegrityError (bare 500)."""
    from shared.services import model_grants as mg
    from shared.db import get_db_session_for_tenant

    tenant = str(uuid.uuid4())
    ws_a, _ = await _seed_org_workspace_project(tenant, "Unit A")
    provider = await _seed_provider(tenant, ["claude-sonnet-4-6"])

    await mg.set_org_grants(
        tenant,
        [{"provider": "anthropic", "model_id": "claude-sonnet-4-6", "credential_id": provider["id"], "visibility": "global", "business_unit_ids": []}],
        created_by="admin1",
    )

    result = await mg.set_bu_grants(
        tenant, ws_a,
        [{"provider": "anthropic", "model_id": "claude-sonnet-4-6", "credential_id": provider["id"]}],
    )
    assert any(e["model_id"] == "claude-sonnet-4-6" for e in result)

    # No duplicate row was created for the key — still exactly the one `global` row.
    async with get_db_session_for_tenant(tenant) as s:
        count = (await s.execute(
            text("SELECT count(*) FROM org_model_grants WHERE tenant_id = :t AND model_id = 'claude-sonnet-4-6'"),
            {"t": tenant},
        )).scalar()
    assert count == 1


@pytest.mark.asyncio
async def test_set_bu_grants_unknown_credential_raises_value_error():
    """I2(b): an unknown credential_id must raise a clean ValueError (mapped to 422 at
    set_bu_grants_route), matching the validation set_org_grants already performs —
    not an unmapped FK IntegrityError."""
    from shared.services import model_grants as mg

    tenant = str(uuid.uuid4())
    ws_a, _ = await _seed_org_workspace_project(tenant, "Unit A")

    with pytest.raises(ValueError):
        await mg.set_bu_grants(
            tenant, ws_a,
            [{"provider": "anthropic", "model_id": "claude-sonnet-4-6", "credential_id": str(uuid.uuid4())}],
        )


# ---------------------------------------------------------------------------
# Final-review fix round: I3 — the fail-closed "grants configured, no project_id"
# branch of effective_project_offerings was never directly asserted.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_effective_project_offerings_fails_closed_without_project_id():
    """Given a tenant with at least one grant configured, calling with project_id=None
    must return set() (fail closed) — not None (which would mean 'stay fully open') and
    not a non-empty set."""
    from shared.services import model_grants as mg

    tenant = str(uuid.uuid4())
    _, proj_a = await _seed_org_workspace_project(tenant, "Unit A")
    provider = await _seed_provider(tenant, ["claude-sonnet-4-6"])
    await mg.set_org_grants(
        tenant,
        [{"provider": "anthropic", "model_id": "claude-sonnet-4-6", "credential_id": provider["id"], "visibility": "global", "business_unit_ids": []}],
        created_by="admin1",
    )

    result = await mg.effective_project_offerings(tenant, None)
    assert result == set()


# ---------------------------------------------------------------------------
# Final-review fix round: I5 — grant matrix's credentialHasKey must reflect
# whether the credential actually holds a stored secret, not a hardcoded True.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_grant_matrix_credential_has_key_reflects_secret_ref():
    from shared.services import model_grants as mg
    from shared.services import model_config as mc

    tenant = str(uuid.uuid4())
    await _seed_org_workspace_project(tenant, "Unit A")
    keyless = await mc.create_provider(
        tenant, provider="anthropic", display_name=f"keyless-{uuid.uuid4().hex[:8]}",
        api_key=None, enabled_models=["claude-sonnet-4-6"], created_by="admin1",
    )

    matrix = await mg.get_grant_matrix(tenant)
    row = next(r for r in matrix["rows"] if r["credential_id"] == keyless["id"])
    assert row["credentialHasKey"] is False


# ---------------------------------------------------------------------------
# Final-review fix round: cheap fix — a malformed project_id must raise a clean
# ValueError (the router's existing `except ValueError` handlers then 404 it),
# not a raw DBAPIError from the SQL layer's UUID cast.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_project_workspace_id_malformed_uuid_raises_value_error():
    from shared.services import model_grants as mg

    tenant = str(uuid.uuid4())
    with pytest.raises(ValueError):
        await mg.get_project_selection(tenant, "not-a-uuid")
