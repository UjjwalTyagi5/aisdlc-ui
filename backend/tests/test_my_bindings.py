"""`GET /auth/bindings` — the caller's own bindings, each with the role it grants.

WHY IT EXISTS. `/api/auth/access-scope` had to report a role per binding and had no
source for one: FastAPI exposed the caller's permissions and their single effective
platform role, never which role they hold WHERE. So it stamped that one role onto every
binding, and a Project Admin in one unit who merely contributes to a project in another
was shown "Project Admin · You administer" on both. An access page that overstates
authority is wrong in the one direction that matters.

THE TEST THAT ACTUALLY BITES is the mixed-roles one: a single role for every binding
passes any test written with a single binding, which is how the bug survived.
"""
from __future__ import annotations

import sys
import uuid as _uuid
from pathlib import Path

import pytest
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.db import get_db_session_superuser  # noqa: E402

pytestmark = pytest.mark.usefixtures("purge_created_orgs")


@pytest.fixture(autouse=True)
async def _dispose_shared_engine():
    yield
    from shared.db import engine
    await engine.dispose()


@pytest.fixture
async def tenant():
    """An org with two units and a project in each."""
    org = str(_uuid.uuid4())
    lending, payments = str(_uuid.uuid4()), str(_uuid.uuid4())
    proj_a, proj_b = str(_uuid.uuid4()), str(_uuid.uuid4())
    async with get_db_session_superuser() as s:
        await s.execute(text(
            "INSERT INTO organizations (id, slug, display_name) VALUES (:i, :s, 'Bindings')"
        ), {"i": org, "s": f"bind-{org[:8]}"})
        for wid, name in ((lending, "Lending"), (payments, "Payments")):
            await s.execute(text(
                "INSERT INTO workspaces (id, organization_id, slug, display_name) "
                "VALUES (:i, :o, :s, :n)"
            ), {"i": wid, "o": org, "s": name.lower(), "n": name})
        await s.execute(
            text("SELECT set_config('app.current_tenant_id', :t, true)"), {"t": org})
        for pid, wid, name in ((proj_a, lending, "test-demo"), (proj_b, payments, "Core ledger")):
            await s.execute(text(
                "INSERT INTO projects (id, workspace_id, tenant_id, display_name) "
                "VALUES (:i, :w, :t, :n)"
            ), {"i": pid, "w": wid, "t": org, "n": name})
        await s.commit()
    yield {"org": org, "lending": lending, "payments": payments,
           "proj_a": proj_a, "proj_b": proj_b}


async def _bind(org, user, kind, scope_id, role):
    async with get_db_session_superuser() as s:
        await s.execute(
            text("SELECT set_config('app.current_tenant_id', :t, true)"), {"t": org})
        await s.execute(text(
            "INSERT INTO role_bindings "
            "  (id, user_id, scope_kind, scope_id, role_name, tier, status, tenant_id) "
            "VALUES (gen_random_uuid(), :u, :k, :s, :r, 'delivery', 'active', "
            "        CAST(:t AS uuid))"
        ), {"u": user, "k": kind, "s": scope_id, "r": role, "t": org})
        await s.commit()


async def _call(org: str, user: str):
    import httpx
    from config.auth.jwt import create_access_token
    from process_api import app

    token = create_access_token(
        user_id=user, tenant_id=org, permissions=["artifact:view"],
        platform_role="project_admin",
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as cl:
        return await cl.get("/auth/bindings", headers={"Authorization": f"Bearer {token}"})


async def test_each_binding_carries_its_own_role(tenant):
    """THE POINT. Two bindings, two DIFFERENT roles — the shape that a single-binding
    test cannot distinguish from stamping one role on everything."""
    user = f"u-{_uuid.uuid4()}"
    await _bind(tenant["org"], user, "business_unit", tenant["lending"], "project_admin")
    await _bind(tenant["org"], user, "project", tenant["proj_b"], "data_engineer")

    r = await _call(tenant["org"], user)

    assert r.status_code == 200, r.text
    by_scope = {b["scopeName"]: b["role"] for b in r.json()}
    assert by_scope == {"Lending": "project_admin", "Core ledger": "data_engineer"}


async def test_the_project_carries_its_parent_unit(tenant):
    """"in Payments" is how the page tells two same-named projects apart, and the parent
    is not on the binding — it comes from the project's own workspace."""
    user = f"u-{_uuid.uuid4()}"
    await _bind(tenant["org"], user, "project", tenant["proj_b"], "data_engineer")

    row = (await _call(tenant["org"], user)).json()[0]

    assert row["parentName"] == "Payments"
    assert row["parentId"] == tenant["payments"]


async def test_a_business_unit_binding_has_no_parent(tenant):
    """Non-vacuity for the above: `parentName` is a real lookup, not a constant."""
    user = f"u-{_uuid.uuid4()}"
    await _bind(tenant["org"], user, "business_unit", tenant["lending"], "project_admin")

    row = (await _call(tenant["org"], user)).json()[0]

    assert row["parentName"] is None


async def test_it_reads_through_row_level_security(tenant):
    """THE FAILURE THIS ROUTE HIT FIRST, and it is silent: `role_bindings` has FORCE ROW
    LEVEL SECURITY keyed on `app.current_tenant_id`, and the app role is not a Postgres
    superuser — so without the GUC the query returns ZERO ROWS rather than an error, and
    the page reports "no bindings" for somebody holding three.

    Three bindings in, three out, is what proves the GUC is being set."""
    user = f"u-{_uuid.uuid4()}"
    await _bind(tenant["org"], user, "business_unit", tenant["lending"], "project_admin")
    await _bind(tenant["org"], user, "project", tenant["proj_a"], "project_admin")
    await _bind(tenant["org"], user, "project", tenant["proj_b"], "data_engineer")

    assert len((await _call(tenant["org"], user)).json()) == 3


async def test_it_returns_only_the_callers_own(tenant):
    """No parameters at all — the user id comes from the JWT and nowhere else, so this
    cannot be pointed at somebody else's bindings."""
    me, someone_else = f"u-{_uuid.uuid4()}", f"u-{_uuid.uuid4()}"
    await _bind(tenant["org"], me, "business_unit", tenant["lending"], "project_admin")
    await _bind(tenant["org"], someone_else, "project", tenant["proj_b"], "data_engineer")

    rows = (await _call(tenant["org"], me)).json()

    assert [b["scopeName"] for b in rows] == ["Lending"]
