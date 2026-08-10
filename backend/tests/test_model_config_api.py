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
