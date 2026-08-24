"""Phase 2 — Model Provider config service + API tests (live Postgres, verify mocked)."""
import uuid
import pytest
from sqlalchemy import text


@pytest.fixture(autouse=True)
async def _dispose_shared_engine():
    yield
    from shared.db import engine
    await engine.dispose()


async def _is_superuser() -> bool:
    from shared.db import get_db_session_superuser
    async with get_db_session_superuser() as s:
        return bool((await s.execute(text("SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user"))).scalar())


# ---------------------------------------------------------------------------
# Task 1: create + list
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_and_list_provider():
    from shared.services import model_config as mc

    tenant = str(uuid.uuid4())
    created = await mc.create_provider(
        tenant, provider="anthropic", display_name="Acme Anthropic",
        api_key="sk-test-123", enabled_models=["claude-sonnet-4-6", "claude-haiku-4-5-20251001"],
        created_by="admin1",
    )
    assert created["provider"] == "anthropic"
    assert created["status"] == "unverified"
    assert "api_key" not in created          # secret never echoed
    assert {o["model_id"] for o in created["offerings"]} == {
        "claude-sonnet-4-6", "claude-haiku-4-5-20251001"
    }

    listed = await mc.list_providers(tenant)
    assert any(p["id"] == created["id"] for p in listed)
    # the key is retrievable from the secret store under the provider's secret_ref
    from shared.services.secret_store import get_secret
    if not await _is_superuser():
        assert await get_secret(tenant, created["secret_ref"]) == "sk-test-123"


@pytest.mark.asyncio
async def test_create_rejects_invalid_model_for_provider():
    from shared.services import model_config as mc
    from shared.services.model_config import InvalidModelError

    tenant = str(uuid.uuid4())
    with pytest.raises(InvalidModelError):
        await mc.create_provider(
            tenant, provider="anthropic", display_name="bad",
            api_key="k", enabled_models=["gpt-4o"], created_by="a",  # gpt-4o is not anthropic
        )


# ---------------------------------------------------------------------------
# Task 2: verify
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_verify_marks_valid_then_invalid(monkeypatch):
    from shared.services import model_config as mc

    tenant = str(uuid.uuid4())
    created = await mc.create_provider(
        tenant, provider="anthropic", display_name="V", api_key="sk-x",
        enabled_models=["claude-sonnet-4-6"], created_by="a",
    )
    pid = created["id"]

    async def _ok(provider, model, api_key):
        return True
    monkeypatch.setattr(mc, "_probe_model", _ok)
    res = await mc.verify_provider(tenant, pid)
    assert res["status"] == "valid"

    async def _bad(provider, model, api_key):
        return False
    monkeypatch.setattr(mc, "_probe_model", _bad)
    res = await mc.verify_provider(tenant, pid)
    assert res["status"] == "invalid"


@pytest.mark.asyncio
async def test_verify_unknown_provider_raises():
    from shared.services import model_config as mc
    from shared.services.model_config import ProviderNotFoundError
    with pytest.raises(ProviderNotFoundError):
        await mc.verify_provider(str(uuid.uuid4()), str(uuid.uuid4()))


# ---------------------------------------------------------------------------
# Task 3: update / set_default / delete
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_update_reconciles_enabled_models_and_rename():
    from shared.services import model_config as mc
    tenant = str(uuid.uuid4())
    created = await mc.create_provider(
        tenant, provider="anthropic", display_name="Orig", api_key="k",
        enabled_models=["claude-sonnet-4-6"], created_by="a",
    )
    pid = created["id"]
    updated = await mc.update_provider(
        tenant, pid, display_name="Renamed",
        enabled_models=["claude-sonnet-4-6", "claude-opus-4-8"],
    )
    assert updated["display_name"] == "Renamed"
    assert {o["model_id"] for o in updated["offerings"] if o["enabled"]} == {
        "claude-sonnet-4-6", "claude-opus-4-8"
    }


@pytest.mark.asyncio
async def test_set_default_is_unique_per_tenant():
    from shared.services import model_config as mc
    tenant = str(uuid.uuid4())
    p = await mc.create_provider(
        tenant, provider="anthropic", display_name="D", api_key="k",
        enabled_models=["claude-sonnet-4-6", "claude-opus-4-8"], created_by="a",
    )
    off = {o["model_id"]: o["id"] for o in p["offerings"]}
    await mc.set_default(tenant, off["claude-sonnet-4-6"])
    await mc.set_default(tenant, off["claude-opus-4-8"])  # must move the default, not add a second
    listed = await mc.list_providers(tenant)
    defaults = [o for pr in listed for o in pr["offerings"] if o["is_default"]]
    assert len(defaults) == 1 and defaults[0]["model_id"] == "claude-opus-4-8"


@pytest.mark.asyncio
async def test_delete_removes_provider_secret_and_offerings():
    from shared.services import model_config as mc
    from shared.services.model_config import ProviderNotFoundError
    tenant = str(uuid.uuid4())
    created = await mc.create_provider(
        tenant, provider="anthropic", display_name="X", api_key="k",
        enabled_models=["claude-sonnet-4-6"], created_by="a",
    )
    await mc.delete_provider(tenant, created["id"])
    listed = await mc.list_providers(tenant)
    assert all(p["id"] != created["id"] for p in listed)
    with pytest.raises(ProviderNotFoundError):
        await mc.verify_provider(tenant, created["id"])


# ---------------------------------------------------------------------------
# Task 4: get_options
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_options_only_returns_valid_enabled(monkeypatch):
    from shared.services import model_config as mc
    tenant = str(uuid.uuid4())
    created = await mc.create_provider(
        tenant, provider="anthropic", display_name="O", api_key="k",
        enabled_models=["claude-sonnet-4-6"], created_by="a",
    )
    # unverified => not offered
    assert await mc.get_options(tenant) == {
        "options": [], "default_offering_id": None, "default_model_id": None}

    async def _ok(p, m, k):
        return True
    monkeypatch.setattr(mc, "_probe_model", _ok)
    await mc.verify_provider(tenant, created["id"])
    off_id = created["offerings"][0]["id"]
    await mc.set_default(tenant, off_id)

    opts = await mc.get_options(tenant)
    assert opts["default_model_id"] == "claude-sonnet-4-6"
    assert opts["default_offering_id"] == off_id
    # Each option now carries full offering identity for unambiguous selection.
    assert any(
        o["model_id"] == "claude-sonnet-4-6"
        and o["provider"] == "anthropic"
        and o["offering_id"] == off_id
        and o["display_name"] == "O"
        and o["provider_id"] == created["id"]
        for o in opts["options"]
    )


@pytest.mark.asyncio
async def test_get_options_stays_tenant_wide_without_project_id_even_with_grants(monkeypatch):
    """Regression guard: get_options has no real project-scoped caller today (the
    router never passes project_id). A tenant that has configured ANY org_model_grants
    row must NOT see its picker go empty just because no project_id was given —
    effective_project_offerings(tenant, None) intentionally fails closed to set() for a
    real RUN, but get_options must not call it at all when project_id is falsy."""
    from shared.services import model_config as mc
    from shared.services import model_grants as mg

    tenant = str(uuid.uuid4())
    created = await mc.create_provider(
        tenant, provider="anthropic", display_name="O2", api_key="k",
        enabled_models=["claude-sonnet-4-6"], created_by="a",
    )

    async def _ok(p, m, k):
        return True
    monkeypatch.setattr(mc, "_probe_model", _ok)
    await mc.verify_provider(tenant, created["id"])

    # Configure a grant row — this is what flips effective_project_offerings(t, None)
    # from None to set() once a project_id IS passed. Here none is passed at all.
    await mg.set_org_grants(
        tenant,
        [{"provider": "anthropic", "model_id": "claude-sonnet-4-6",
          "credential_id": created["id"], "visibility": "global", "business_unit_ids": []}],
        created_by="admin1",
    )

    opts = await mc.get_options(tenant)
    assert opts["options"], "options must stay tenant-wide when no project_id is given"
    assert any(o["model_id"] == "claude-sonnet-4-6" for o in opts["options"])


# ---------------------------------------------------------------------------
# Task 5: router + RBAC + registration
# ---------------------------------------------------------------------------
from httpx import ASGITransport, AsyncClient


def _model_app(perms, tenant_id):
    from fastapi import FastAPI, Request
    from shared.routers.model import model_router, model_options_router

    app = FastAPI()

    @app.middleware("http")
    async def _inject(request: Request, call_next):
        request.state.permissions = perms
        request.state.tenant_id = tenant_id
        request.state.user_id = "admin1"
        return await call_next(request)

    app.include_router(model_router)
    app.include_router(model_options_router)
    return app


@pytest.mark.asyncio
async def test_catalog_endpoint_lists_providers():
    app = _model_app(["model:manage"], str(uuid.uuid4()))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/model/catalog")
    assert r.status_code == 200
    assert any(p["provider"] == "anthropic" for p in r.json())


@pytest.mark.asyncio
async def test_create_then_list_via_api(monkeypatch):
    import shared.services.model_config as mc
    tenant = str(uuid.uuid4())
    app = _model_app(["model:manage"], tenant)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/model/providers", json={
            "provider": "anthropic", "display_name": "Acme", "api_key": "sk-1",
            "enabled_models": ["claude-sonnet-4-6"],
        })
        assert r.status_code == 201, r.text
        body = r.json()
        assert "api_key" not in body and "secret_ref" not in body  # secrets not exposed over HTTP
        r2 = await c.get("/model/providers")
    assert r2.status_code == 200
    assert any(p["display_name"] == "Acme" for p in r2.json())


@pytest.mark.asyncio
async def test_options_requires_run_create_not_model_manage():
    tenant = str(uuid.uuid4())
    # a user with only run:create can read options
    app = _model_app(["run:create"], tenant)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/model/options")
    assert r.status_code == 200
    # ...but cannot create a provider (model:manage required)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/model/providers", json={
            "provider": "anthropic", "display_name": "x", "api_key": "k", "enabled_models": [],
        })
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# Task 3: keyless onboarding + workspace-scoped listing
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_provider_without_api_key_has_no_key():
    from shared.services import model_config as mc
    import uuid
    tenant = str(uuid.uuid4())

    created = await mc.create_provider(
        tenant, provider="anthropic", display_name="Keyless",
        api_key=None, enabled_models=["claude-sonnet-4-6"], created_by="admin1",
    )
    assert created["secret_ref"] is None


@pytest.mark.asyncio
async def test_create_provider_scoped_to_workspace():
    from shared.services import model_config as mc
    import uuid
    tenant = str(uuid.uuid4())
    ws_id = str(uuid.uuid4())

    created = await mc.create_provider(
        tenant, provider="anthropic", display_name="BU Key",
        api_key="sk-x", enabled_models=["claude-sonnet-4-6"], created_by="admin1",
        workspace_id=ws_id,
    )
    providers = await mc.list_providers(tenant, workspace_id=ws_id)
    assert any(p["id"] == created["id"] for p in providers)

    other_ws_providers = await mc.list_providers(tenant, workspace_id=str(uuid.uuid4()))
    # A different BU sees org-wide connections only — this BU-scoped one is absent.
    assert not any(p["id"] == created["id"] for p in other_ws_providers)


@pytest.mark.asyncio
async def test_list_providers_scope_all_returns_every_connection():
    from shared.services import model_config as mc
    import uuid
    tenant = str(uuid.uuid4())
    org_wide = await mc.create_provider(
        tenant, provider="anthropic", display_name="Org Wide",
        api_key="sk-x", enabled_models=["claude-sonnet-4-6"], created_by="admin1",
    )
    bu_scoped = await mc.create_provider(
        tenant, provider="openai", display_name="BU Scoped",
        api_key="sk-y", enabled_models=["gpt-4o"], created_by="admin1",
        workspace_id=str(uuid.uuid4()),
    )
    providers = await mc.list_providers(tenant, scope="all")
    ids = {p["id"] for p in providers}
    assert org_wide["id"] in ids and bu_scoped["id"] in ids


# ---------------------------------------------------------------------------
# Task 5: cascade router endpoints (real JWTs via process_api, matching the
# mint_token + ASGITransport pattern used elsewhere in the test suite)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_org_grants_roundtrip_via_router(mint_token):
    import httpx
    from process_api import app
    from shared.services import model_config as mc
    import uuid

    tenant = str(uuid.uuid4())
    created = await mc.create_provider(
        tenant, provider="anthropic", display_name="Acme", api_key="sk-x",
        enabled_models=["claude-sonnet-4-6"], created_by="admin1",
    )
    token = mint_token(tenant_id=tenant, permissions=["model:manage"])
    headers = {"Authorization": f"Bearer {token}"}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        put_resp = await client.put(
            "/model/allowed/org",
            json={"entries": [{
                "provider": "anthropic", "model_id": "claude-sonnet-4-6",
                "credential_id": created["id"], "visibility": "global", "business_unit_ids": [],
            }]},
            headers=headers,
        )
        assert put_resp.status_code == 200

        get_resp = await client.get("/model/allowed/org", headers=headers)
        assert get_resp.status_code == 200
        assert len(get_resp.json()) == 1


@pytest.mark.asyncio
async def test_allowed_project_requires_run_create_not_model_manage(mint_token):
    """A caller with run:create but WITHOUT model:manage can still read/write their
    project's model selection (spec §4 permission split)."""
    import httpx
    from process_api import app
    import uuid

    tenant = str(uuid.uuid4())
    token = mint_token(tenant_id=tenant, permissions=["run:create"])
    headers = {"Authorization": f"Bearer {token}"}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/model/allowed/project", params={"projectId": str(uuid.uuid4())}, headers=headers
        )
        # 404/422 (unknown project) is fine — the point is it's NOT 403.
        assert resp.status_code != 403


# ---------------------------------------------------------------------------
# Final-review fix round: C1 — field-name mismatch across the grants cascade.
# frontend/lib/schemas/model.ts's Zod schemas require camelCase
# (credentialId/credentialName/businessUnitIds); model_grants.py's dicts are
# snake_case with no response_model, so the router must rename explicitly.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_org_grants_response_uses_camel_case_keys(mint_token):
    """THE test that would have caught C1: raw JSON from PUT and GET
    /model/allowed/org must contain the literal keys "credentialId" and
    "businessUnitIds" — never "credential_id" / "business_unit_ids"."""
    import httpx
    from process_api import app
    from shared.services import model_config as mc
    import uuid

    tenant = str(uuid.uuid4())
    created = await mc.create_provider(
        tenant, provider="anthropic", display_name="C1", api_key="sk-x",
        enabled_models=["claude-sonnet-4-6"], created_by="admin1",
    )
    token = mint_token(tenant_id=tenant, permissions=["model:manage"])
    headers = {"Authorization": f"Bearer {token}"}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        # Input side: the frontend sends camelCase (credentialId/businessUnitIds) — the
        # Pydantic alias must accept it.
        put_resp = await client.put(
            "/model/allowed/org",
            json={"entries": [{
                "provider": "anthropic", "model_id": "claude-sonnet-4-6",
                "credentialId": created["id"], "visibility": "specific",
                "businessUnitIds": [str(uuid.uuid4())],
            }]},
            headers=headers,
        )
        assert put_resp.status_code == 200, put_resp.text
        assert '"credentialId"' in put_resp.text
        assert '"businessUnitIds"' in put_resp.text
        assert '"credential_id"' not in put_resp.text
        assert '"business_unit_ids"' not in put_resp.text

        get_resp = await client.get("/model/allowed/org", headers=headers)
        assert get_resp.status_code == 200
        assert '"credentialId"' in get_resp.text
        assert '"credential_id"' not in get_resp.text
        entry = get_resp.json()[0]
        assert entry["credentialId"] == created["id"]
        assert entry["businessUnitIds"]


@pytest.mark.asyncio
async def test_bu_availability_matrix_and_project_use_camel_case(mint_token):
    """C1, continued: GET /allowed/bu, /availability (I1, corrected: accepts EITHER
    model:manage — a Business Unit Admin's own governance view — OR run:create — the
    run-time picker/create-project dialog; neither alone is the right single gate),
    /grant-matrix, and /allowed/project must all rename credential_id/credential_name to
    camelCase; /availability's entries must also carry "visibility" (get_availability
    never included it at all before this fix)."""
    import httpx
    from process_api import app
    from shared.authz.grant import grant_role
    from shared.services import model_config as mc
    from shared.services import model_grants as mg
    from tests.test_model_grants import _seed_org_workspace_project
    import uuid

    tenant = str(uuid.uuid4())
    ws_id, proj_id = await _seed_org_workspace_project(tenant, "Unit A")
    created = await mc.create_provider(
        tenant, provider="anthropic", display_name="C1b", api_key="sk-x",
        enabled_models=["claude-sonnet-4-6"], created_by="admin1",
    )
    await mg.set_org_grants(
        tenant,
        [{"provider": "anthropic", "model_id": "claude-sonnet-4-6",
          "credential_id": created["id"], "visibility": "global", "business_unit_ids": []}],
        created_by="admin1",
    )

    # GET /model/allowed/bu is now can_perform-scoped (design-doc gap #1, closed) — the
    # JWT permission claim alone is no longer enough; a real role_bindings row at THIS
    # business unit is required. bu_admin carries model:manage.
    mgmt_user = f"bu-admin-{uuid.uuid4()}"
    await grant_role(mgmt_user, ws_id, "bu_admin", tenant_id=tenant, scope_kind="business_unit")
    mgmt_headers = {"Authorization": f"Bearer {mint_token(user_id=mgmt_user, tenant_id=tenant, permissions=['model:manage'])}"}
    # GET /model/allowed/project is also can_perform-scoped now (run:create, at project
    # scope) — this user additionally needs a role_bindings row on proj_id itself.
    run_user = f"developer-{uuid.uuid4()}"
    await grant_role(run_user, proj_id, "developer", tenant_id=tenant, scope_kind="project")
    run_headers = {"Authorization": f"Bearer {mint_token(user_id=run_user, tenant_id=tenant, permissions=['run:create'])}"}
    no_perm_headers = {"Authorization": f"Bearer {mint_token(tenant_id=tenant, permissions=[])}"}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        bu_resp = await client.get("/model/allowed/bu", params={"workspaceId": ws_id}, headers=mgmt_headers)
        assert bu_resp.status_code == 200
        assert '"credentialId"' in bu_resp.text and '"credential_id"' not in bu_resp.text

        # I1, corrected: /model/availability accepts EITHER model:manage (a Business Unit
        # Admin's own governance view) OR run:create (the run-time picker) — a caller
        # holding neither must still be denied.
        no_access = await client.get("/model/availability", params={"workspaceId": ws_id}, headers=no_perm_headers)
        assert no_access.status_code == 403

        mgmt_avail_resp = await client.get("/model/availability", params={"workspaceId": ws_id}, headers=mgmt_headers)
        assert mgmt_avail_resp.status_code == 200, mgmt_avail_resp.text

        avail_resp = await client.get("/model/availability", params={"workspaceId": ws_id}, headers=run_headers)
        assert avail_resp.status_code == 200, avail_resp.text
        assert '"credentialId"' in avail_resp.text and '"credential_id"' not in avail_resp.text
        avail_body = avail_resp.json()
        assert avail_body and all("visibility" in e for e in avail_body)

        matrix_resp = await client.get("/model/grant-matrix", headers=mgmt_headers)
        assert matrix_resp.status_code == 200
        assert '"credentialId"' in matrix_resp.text and '"credential_id"' not in matrix_resp.text
        assert any(r["credentialHasKey"] is True for r in matrix_resp.json()["rows"])

        proj_resp = await client.get("/model/allowed/project", params={"projectId": proj_id}, headers=run_headers)
        assert proj_resp.status_code == 200, proj_resp.text
        assert '"credentialId"' in proj_resp.text and '"credential_id"' not in proj_resp.text
        assert '"defaultKey"' in proj_resp.text


@pytest.mark.asyncio
async def test_bu_admin_cannot_edit_a_different_business_unit(mint_token):
    """The point of wiring can_perform into /model/allowed/bu (design-doc gap #1,
    closed): holding model:manage in the tenant used to be enough to edit ANY
    business unit's grants. A BU Admin scoped to Unit A must now be denied on
    Unit B's grants, while their own unit keeps working — and an Organization
    Admin (bound at the organization scope, which is an ancestor of every BU)
    is untouched by this change."""
    import httpx
    from process_api import app
    from shared.authz.grant import grant_role
    from tests.test_model_grants import _seed_org_workspace_project
    import uuid

    tenant = str(uuid.uuid4())
    ws_a, _ = await _seed_org_workspace_project(tenant, "Unit A")
    ws_b, _ = await _seed_org_workspace_project(tenant, "Unit B")

    bu_admin_a = f"bu-admin-a-{uuid.uuid4()}"
    await grant_role(bu_admin_a, ws_a, "bu_admin", tenant_id=tenant, scope_kind="business_unit")
    bu_a_headers = {"Authorization": f"Bearer {mint_token(user_id=bu_admin_a, tenant_id=tenant, permissions=['model:manage'])}"}

    org_admin = f"org-admin-{uuid.uuid4()}"
    await grant_role(org_admin, tenant, "org_admin", tenant_id=tenant, scope_kind="organization")
    org_headers = {"Authorization": f"Bearer {mint_token(user_id=org_admin, tenant_id=tenant, permissions=['admin:*'])}"}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        own_unit = await client.get("/model/allowed/bu", params={"workspaceId": ws_a}, headers=bu_a_headers)
        assert own_unit.status_code == 200, own_unit.text

        other_unit = await client.get("/model/allowed/bu", params={"workspaceId": ws_b}, headers=bu_a_headers)
        assert other_unit.status_code == 403

        other_unit_put = await client.put(
            "/model/allowed/bu", params={"workspaceId": ws_b}, json={"entries": []}, headers=bu_a_headers,
        )
        assert other_unit_put.status_code == 403

        # Org Admin reaches every BU via the organization-scope ancestor rule.
        org_sees_a = await client.get("/model/allowed/bu", params={"workspaceId": ws_a}, headers=org_headers)
        assert org_sees_a.status_code == 200
        org_sees_b = await client.get("/model/allowed/bu", params={"workspaceId": ws_b}, headers=org_headers)
        assert org_sees_b.status_code == 200


# ---------------------------------------------------------------------------
# Final-review fix round: C2 — ProviderOut was missing workspace_id/hasKey/
# approval_* even though model_providers has had the approval columns since
# Task 1's migration, and model_config.py already had workspace_id in hand.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_provider_out_roundtrips_workspace_and_haskey(mint_token):
    """A BU-scoped connection must round-trip workspaceId as the REAL workspace id and
    hasKey correctly. Before the C2 fix, ProviderOut had no such fields at all, so the
    frontend's Zod defaults silently fabricated workspaceId=null / hasKey=true instead of
    the response ever saying so — the dangerous, silent kind of bug.

    Updated for Task 4: BU-scoped creation now requires real ownership of the target
    workspace, a model_provider grant, and a real api_key — a keyless BU-scoped
    connection is no longer reachable through this route at all (see
    test_bu_scoped_provider_creation_requires_api_key), so this now exercises the fixed
    happy path (real key -> hasKey True) instead of the keyless one it used to."""
    import httpx
    from process_api import app
    from shared.authz.grant import grant_role
    from tests.test_model_grants import _seed_org_workspace_project
    import uuid

    tenant = str(uuid.uuid4())
    ws_id, _ = await _seed_org_workspace_project(tenant, "Unit A")

    bu_admin = f"bu-admin-{uuid.uuid4()}"
    await grant_role(bu_admin, ws_id, "bu_admin", tenant_id=tenant, scope_kind="business_unit")
    headers = {"Authorization": f"Bearer {mint_token(user_id=bu_admin, tenant_id=tenant, permissions=['model:manage'])}"}

    org_admin = f"org-admin-{uuid.uuid4()}"
    org_headers = {"Authorization": f"Bearer {mint_token(user_id=org_admin, tenant_id=tenant, permissions=['admin:*'])}"}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        grant_resp = await client.put(
            "/model/providers/grants", params={"workspaceId": ws_id}, json={"providers": ["anthropic"]},
            headers=org_headers,
        )
        assert grant_resp.status_code == 200, grant_resp.text

        create_resp = await client.post(
            "/model/providers",
            json={
                "provider": "anthropic", "display_name": "C2 BU key", "api_key": "sk-test-123",
                "enabled_models": ["claude-sonnet-4-6"], "workspaceId": ws_id,
            },
            headers=headers,
        )
        assert create_resp.status_code == 201, create_resp.text
        body = create_resp.json()
        assert body["workspaceId"] == ws_id
        assert body["hasKey"] is True
        assert body["approvalStatus"] == "active"
        assert body["approvalDecidedBy"] is None
        assert body["approvalDecidedAt"] is None
        assert body["approvalReason"] is None

        list_resp = await client.get("/model/providers", params={"scope": "all"}, headers=headers)
        assert list_resp.status_code == 200
        row = next(p for p in list_resp.json() if p["id"] == body["id"])
        assert row["workspaceId"] == ws_id
        assert row["hasKey"] is True


# ---------------------------------------------------------------------------
# Final-review fix round: I4 — get_options_route never threaded projectId
# through to mc.get_options, so the project-scoped narrowing was unreachable
# from the real HTTP surface even though the service function supported it.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_options_route_threads_project_id(mint_token, monkeypatch):
    import httpx
    from process_api import app
    from shared.services import model_config as mc
    from shared.services import model_grants as mg
    from tests.test_model_grants import _seed_org_workspace_project
    import uuid

    tenant = str(uuid.uuid4())
    _, proj_id = await _seed_org_workspace_project(tenant, "Unit A")
    created = await mc.create_provider(
        tenant, provider="anthropic", display_name="I4", api_key="sk-x",
        enabled_models=["claude-sonnet-4-6", "claude-opus-4-8"], created_by="admin1",
    )

    async def _ok(p, m, k):
        return True
    monkeypatch.setattr(mc, "_probe_model", _ok)
    await mc.verify_provider(tenant, created["id"])

    # Only claude-sonnet-4-6 is granted at all — claude-opus-4-8 has no org grant.
    await mg.set_org_grants(
        tenant,
        [{"provider": "anthropic", "model_id": "claude-sonnet-4-6",
          "credential_id": created["id"], "visibility": "global", "business_unit_ids": []}],
        created_by="admin1",
    )

    headers = {"Authorization": f"Bearer {mint_token(tenant_id=tenant, permissions=['run:create'])}"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        r_wide = await client.get("/model/options", headers=headers)
        assert r_wide.status_code == 200
        assert {o["model_id"] for o in r_wide.json()["options"]} == {"claude-sonnet-4-6", "claude-opus-4-8"}

        r_scoped = await client.get("/model/options", params={"projectId": proj_id}, headers=headers)
        assert r_scoped.status_code == 200
        assert {o["model_id"] for o in r_scoped.json()["options"]} == {"claude-sonnet-4-6"}


@pytest.mark.asyncio
async def test_list_providers_malformed_workspace_id_does_not_crash():
    """Discovered via persona testing: a Business Unit Admin's active workspace can
    resolve to a not-yet-migrated identity (e.g. a Business Units fixture id) rather
    than a real backend UUID. Passing that straight into a UUID-typed SQL comparison
    used to raise an unhandled asyncpg.DataError -> bare 500. Falling back to the
    org-wide-only view is safe: it never surfaces a BU-specific connection."""
    from shared.services import model_config as mc
    import uuid

    tenant = str(uuid.uuid4())
    org_wide = await mc.create_provider(
        tenant, provider="anthropic", display_name="Org Wide",
        api_key="sk-x", enabled_models=["claude-sonnet-4-6"], created_by="admin1",
    )

    providers = await mc.list_providers(tenant, workspace_id="ws_platform")
    assert any(p["id"] == org_wide["id"] for p in providers)


@pytest.mark.asyncio
async def test_create_provider_malformed_workspace_id_rejected_not_widened():
    """The write-path counterpart: silently dropping a malformed workspace_id to None
    would widen a BU-scoped onboarding to org-wide (every unit could suddenly see it),
    the opposite of fail-safe — this must be rejected with a clear error instead."""
    from shared.services import model_config as mc
    import uuid

    tenant = str(uuid.uuid4())
    with pytest.raises(ValueError):
        await mc.create_provider(
            tenant, provider="anthropic", display_name="Bad WS", api_key="sk-x",
            enabled_models=["claude-sonnet-4-6"], created_by="admin1",
            workspace_id="ws_platform",
        )


@pytest.mark.asyncio
async def test_availability_malformed_workspace_id_does_not_crash(mint_token):
    """Same discovery, through the HTTP surface: GET /model/availability with a
    not-yet-migrated workspace id must not 500 — it should read as centrally-credentialed
    only, with nothing locally credentialed (since there's no real backend BU to check)."""
    import httpx
    from process_api import app
    from shared.services import model_config as mc
    from shared.services import model_grants as mg
    import uuid

    tenant = str(uuid.uuid4())
    created = await mc.create_provider(
        tenant, provider="anthropic", display_name="Central", api_key="sk-x",
        enabled_models=["claude-sonnet-4-6"], created_by="admin1",
    )
    # get_availability's centrallyCredentialed check requires status='valid' — mark it so
    # directly, same pattern as test_model_resolver.py's _seed_valid_provider.
    from shared.db import get_db_session_for_tenant
    from sqlalchemy import text
    async with get_db_session_for_tenant(tenant) as s:
        await s.execute(
            text("UPDATE model_providers SET status='valid' WHERE id=:i AND tenant_id=:t"),
            {"i": created["id"], "t": tenant},
        )
    await mg.set_org_grants(
        tenant,
        [{"provider": "anthropic", "model_id": "claude-sonnet-4-6",
          "credential_id": created["id"], "visibility": "global", "business_unit_ids": []}],
        created_by="admin1",
    )

    headers = {"Authorization": f"Bearer {mint_token(tenant_id=tenant, permissions=['model:manage'])}"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/model/availability", params={"workspaceId": "ws_platform"}, headers=headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert any(e["model_id"] == "claude-sonnet-4-6" and e["centrallyCredentialed"] for e in body)


@pytest.mark.asyncio
async def test_availability_accepts_model_manage_for_bu_admin_governance_view(mint_token):
    """/model/availability has two legitimate consumers gated by different permissions:
    a Business Unit Admin's own governance view (model:manage) and the run-time picker /
    create-project dialog (run:create). A caller holding only model:manage must succeed;
    a caller holding neither must still be denied."""
    import httpx
    from process_api import app
    import uuid

    tenant = str(uuid.uuid4())
    mgmt_headers = {"Authorization": f"Bearer {mint_token(tenant_id=tenant, permissions=['model:manage'])}"}
    no_perm_headers = {"Authorization": f"Bearer {mint_token(tenant_id=tenant, permissions=[])}"}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        ok = await client.get("/model/availability", params={"workspaceId": str(uuid.uuid4())}, headers=mgmt_headers)
        assert ok.status_code == 200, ok.text

        denied = await client.get(
            "/model/availability", params={"workspaceId": str(uuid.uuid4())}, headers=no_perm_headers,
        )
        assert denied.status_code == 403


# ---------------------------------------------------------------------------
# Task 4: BU-scoped provider creation is now gated on real ownership of the
# target business unit (via _require_scoped/can_perform, resource_kind=
# "business_unit") AND on a model_provider grant for the requested provider
# (granted_target_refs, kind="model_provider") — closing the gap where any
# tenant-wide model:manage holder could plant a provider connection under ANY
# business unit, with no grant at all. api_key also becomes REQUIRED on the
# BU-scoped path specifically; org-wide creation (workspaceId omitted) keeps
# its existing optional/keyless behaviour unchanged.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_bu_scoped_provider_creation_requires_ownership(mint_token):
    """A caller who administers a DIFFERENT business unit — not the target one — must
    be denied even though model:manage is a tenant-wide permission and even though the
    target BU already holds the grant and a real key is supplied."""
    import httpx
    from process_api import app
    from shared.authz.grant import grant_role
    from tests.test_model_grants import _seed_org_workspace_project
    import uuid

    tenant = str(uuid.uuid4())
    ws_a, _ = await _seed_org_workspace_project(tenant, "Unit A")
    ws_b, _ = await _seed_org_workspace_project(tenant, "Unit B")

    bu_admin_a = f"bu-admin-a-{uuid.uuid4()}"
    await grant_role(bu_admin_a, ws_a, "bu_admin", tenant_id=tenant, scope_kind="business_unit")
    bu_a_headers = {"Authorization": f"Bearer {mint_token(user_id=bu_admin_a, tenant_id=tenant, permissions=['model:manage'])}"}

    org_admin = f"org-admin-{uuid.uuid4()}"
    org_headers = {"Authorization": f"Bearer {mint_token(user_id=org_admin, tenant_id=tenant, permissions=['admin:*'])}"}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        # Grant Unit B (not Unit A) access to anthropic.
        grant_resp = await client.put(
            "/model/providers/grants", params={"workspaceId": ws_b}, json={"providers": ["anthropic"]},
            headers=org_headers,
        )
        assert grant_resp.status_code == 200, grant_resp.text

        resp = await client.post(
            "/model/providers",
            json={
                "provider": "anthropic", "display_name": "Wrong BU", "api_key": "sk-test-123",
                "enabled_models": ["claude-sonnet-4-6"], "workspaceId": ws_b,
            },
            headers=bu_a_headers,
        )
    assert resp.status_code == 403, resp.text


@pytest.mark.asyncio
async def test_bu_scoped_provider_creation_requires_grant(mint_token):
    """The caller administers their OWN business unit but it has no model_provider
    grant at all — this is the bug the whole redesign exists to fix: previously this
    succeeded with a 201."""
    import httpx
    from process_api import app
    from shared.authz.grant import grant_role
    from tests.test_model_grants import _seed_org_workspace_project
    import uuid

    tenant = str(uuid.uuid4())
    ws_id, _ = await _seed_org_workspace_project(tenant, "Unit A")

    bu_admin = f"bu-admin-{uuid.uuid4()}"
    await grant_role(bu_admin, ws_id, "bu_admin", tenant_id=tenant, scope_kind="business_unit")
    headers = {"Authorization": f"Bearer {mint_token(user_id=bu_admin, tenant_id=tenant, permissions=['model:manage'])}"}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/model/providers",
            json={
                "provider": "anthropic", "display_name": "Test key", "api_key": "sk-test-123",
                "enabled_models": ["claude-sonnet-4-6"], "workspaceId": ws_id,
            },
            headers=headers,
        )
    assert resp.status_code == 403, resp.text


@pytest.mark.asyncio
async def test_bu_scoped_provider_creation_requires_api_key(mint_token):
    """The BU owns the resource and holds the grant, but sends no api_key — must 422,
    not silently create a keyless connection the way org-wide onboarding still can."""
    import httpx
    from process_api import app
    from shared.authz.grant import grant_role
    from tests.test_model_grants import _seed_org_workspace_project
    import uuid

    tenant = str(uuid.uuid4())
    ws_id, _ = await _seed_org_workspace_project(tenant, "Unit A")

    bu_admin = f"bu-admin-{uuid.uuid4()}"
    await grant_role(bu_admin, ws_id, "bu_admin", tenant_id=tenant, scope_kind="business_unit")
    bu_headers = {"Authorization": f"Bearer {mint_token(user_id=bu_admin, tenant_id=tenant, permissions=['model:manage'])}"}

    org_admin = f"org-admin-{uuid.uuid4()}"
    org_headers = {"Authorization": f"Bearer {mint_token(user_id=org_admin, tenant_id=tenant, permissions=['admin:*'])}"}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        grant_resp = await client.put(
            "/model/providers/grants", params={"workspaceId": ws_id}, json={"providers": ["anthropic"]},
            headers=org_headers,
        )
        assert grant_resp.status_code == 200, grant_resp.text

        resp = await client.post(
            "/model/providers",
            json={
                "provider": "anthropic", "display_name": "Test key", "api_key": "",
                "enabled_models": ["claude-sonnet-4-6"], "workspaceId": ws_id,
            },
            headers=bu_headers,
        )
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_bu_scoped_provider_creation_succeeds_once_granted(mint_token):
    """Own BU, real grant, real key — the fixed happy path must still 201."""
    import httpx
    from process_api import app
    from shared.authz.grant import grant_role
    from tests.test_model_grants import _seed_org_workspace_project
    import uuid

    tenant = str(uuid.uuid4())
    ws_id, _ = await _seed_org_workspace_project(tenant, "Unit A")

    bu_admin = f"bu-admin-{uuid.uuid4()}"
    await grant_role(bu_admin, ws_id, "bu_admin", tenant_id=tenant, scope_kind="business_unit")
    bu_headers = {"Authorization": f"Bearer {mint_token(user_id=bu_admin, tenant_id=tenant, permissions=['model:manage'])}"}

    org_admin = f"org-admin-{uuid.uuid4()}"
    org_headers = {"Authorization": f"Bearer {mint_token(user_id=org_admin, tenant_id=tenant, permissions=['admin:*'])}"}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        grant_resp = await client.put(
            "/model/providers/grants", params={"workspaceId": ws_id}, json={"providers": ["anthropic"]},
            headers=org_headers,
        )
        assert grant_resp.status_code == 200, grant_resp.text

        resp = await client.post(
            "/model/providers",
            json={
                "provider": "anthropic", "display_name": "Test key", "api_key": "sk-test-123",
                "enabled_models": ["claude-sonnet-4-6"], "workspaceId": ws_id,
            },
            headers=bu_headers,
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["workspaceId"] == ws_id
        assert body["hasKey"] is True


@pytest.mark.asyncio
async def test_org_wide_provider_creation_still_allows_no_key(mint_token):
    """Org-wide creation (workspaceId omitted) is untouched by this gate: no ownership
    check, no grant check, and api_key stays optional — a provider can still be
    onboarded org-wide keyless (spec §2.3), a BU/project supplying its own key later."""
    import httpx
    from process_api import app
    import uuid

    tenant = str(uuid.uuid4())
    headers = {"Authorization": f"Bearer {mint_token(tenant_id=tenant, permissions=['model:manage'])}"}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/model/providers",
            json={
                "provider": "openai", "display_name": "Org-wide, keyless", "api_key": "",
                "enabled_models": ["gpt-4o"],
            },
            headers=headers,
        )
    assert resp.status_code == 201, resp.text
    assert resp.json()["hasKey"] is False
