import uuid as _uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

import process_api
from config.auth.jwt import create_access_token
from shared.db import get_db_session_for_tenant, get_db_session_superuser

pytestmark = pytest.mark.usefixtures("purge_created_orgs")


@pytest.fixture(autouse=True)
async def _dispose_shared_engine():
    yield
    from shared.db import engine
    await engine.dispose()


@pytest.fixture
async def org_and_unit():
    org = str(_uuid.uuid4())
    unit = str(_uuid.uuid4())
    async with get_db_session_superuser() as s:
        await s.execute(text(
            "INSERT INTO organizations (id, slug, display_name) VALUES (:i, :s, 'Track Test')"
        ), {"i": org, "s": f"track-{org[:8]}"})
        await s.execute(text(
            "INSERT INTO workspaces (id, organization_id, slug, display_name) "
            "VALUES (:i, :o, 'unit', 'Unit')"
        ), {"i": unit, "o": org})
    yield {"org": org, "unit": unit}


def _client() -> TestClient:
    return TestClient(process_api.app)


def _hdr(user_id: str, org: str, perms: list[str]) -> dict:
    return {
        "Authorization": "Bearer "
        + create_access_token(user_id=user_id, tenant_id=org, permissions=perms)
    }


def test_creating_a_project_with_a_track_persists_and_returns_it(org_and_unit):
    t = org_and_unit
    user = f"admin-{_uuid.uuid4()}"
    resp = _client().post(
        "/projects",
        json={
            "name": "Warehouse Migration",
            "workspaceId": t["unit"],
            "track": "data_engineering",
            "monthlyBudgetUsd": 1000,
        },
        headers=_hdr(user, t["org"], ["project:create", "admin:*"]),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["track"] == "data_engineering"

    project_id = body["id"]
    reread = _client().get(
        f"/projects/{project_id}", headers=_hdr(user, t["org"], ["admin:*"])
    )
    assert reread.status_code == 200
    assert reread.json()["track"] == "data_engineering"


def test_creating_a_project_without_a_track_defaults_to_greenfield(org_and_unit):
    t = org_and_unit
    user = f"admin-{_uuid.uuid4()}"
    resp = _client().post(
        "/projects",
        json={"name": "No Track Given", "workspaceId": t["unit"], "monthlyBudgetUsd": 1000},
        headers=_hdr(user, t["org"], ["project:create", "admin:*"]),
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["track"] == "greenfield"


def test_an_invalid_track_is_rejected(org_and_unit):
    t = org_and_unit
    user = f"admin-{_uuid.uuid4()}"
    resp = _client().post(
        "/projects",
        json={"name": "Bad Track", "workspaceId": t["unit"], "track": "not_a_real_track"},
        headers=_hdr(user, t["org"], ["project:create", "admin:*"]),
    )
    assert resp.status_code == 422
