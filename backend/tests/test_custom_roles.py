"""Phase 2 — custom (dynamic) roles: models, resolver union, CRUD, RLS."""
from shared.models.orm import (
    CustomRole,
    CustomRolePermission,
    RoleBinding,
    _RLS_TABLES,
)


def test_custom_role_tables_registered_for_rls():
    assert "custom_roles" in _RLS_TABLES
    assert "custom_role_permissions" in _RLS_TABLES


def test_custom_role_columns():
    cols = {c.name for c in CustomRole.__table__.columns}
    assert {"id", "tenant_id", "name", "description", "created_at"} <= cols
    # Owner scope + creator, added in migration 0004.
    assert {"scope_kind", "scope_id", "created_by"} <= cols
    uniques = [
        tuple(sorted(c.name for c in con.columns))
        for con in CustomRole.__table__.constraints
        if con.__class__.__name__ == "UniqueConstraint"
    ]
    # Uniqueness is per OWNER scope, not per tenant: two business units may each
    # define a "Reviewer" without colliding, which the old tenant-wide unique forbade.
    assert ("name", "scope_id", "tenant_id") in uniques


def test_custom_role_permission_columns():
    cols = {c.name for c in CustomRolePermission.__table__.columns}
    assert {"id", "custom_role_id", "permission_name", "tenant_id"} <= cols


def test_user_workspace_role_has_nullable_custom_ref():
    cols = {c.name: c for c in RoleBinding.__table__.columns}
    assert "custom_role_id" in cols
    assert cols["custom_role_id"].nullable is True
    assert cols["role_name"].nullable is True


def test_custom_role_tables_are_rls_protected():
    """custom_roles and custom_role_permissions are tenant-scoped and must be forced.

    Was a shape check on migration 0012. The baseline drives RLS from
    shared.models.orm._RLS_TABLES, so the check now targets that registry — which is
    also what the DDL loop iterates, so the two cannot disagree.
    """
    from shared.models.orm import _RLS_TABLES
    for tbl in ("custom_roles", "custom_role_permissions"):
        assert tbl in _RLS_TABLES, f"{tbl} is tenant-scoped but absent from _RLS_TABLES"

    from pathlib import Path
    text = (Path(__file__).resolve().parents[1] / "migrations" / "versions" / "0001_baseline.py").read_text(encoding="utf-8")
    # All four statements per table — FORCE alone leaves a table wide open.
    for stmt in ("ENABLE ROW LEVEL SECURITY", "FORCE ROW LEVEL SECURITY",
                 "CREATE POLICY tenant_isolation ", "CREATE POLICY tenant_isolation_insert "):
        assert stmt in text, f"baseline is missing: {stmt}"


import os
import uuid as _uuid

import pytest

pg_required = pytest.mark.skipif(
    not os.getenv("POSTGRES_CONN_STRING") and not os.getenv("AZURE_KEY_VAULT_URL"),
    reason="needs a live Postgres",
)

# The DB-backed cases here insert throwaway organizations directly and left them
# behind. The conftest fixture diffs the organizations table around each test; the
# tests that touch no database simply see a no-op.
pytestmark = pytest.mark.usefixtures("purge_created_orgs")

# Cross-tenant RLS *isolation* assertions must run only against the restricted app
# DSN (POSTGRES_CONN_STRING → sdlc_app, non-BYPASSRLS), never the KV-resolved superuser
# engine, which bypasses RLS by design and would falsely report a leak. This mirrors
# test_rls_isolation.py / test_rls_coverage.py, which gate every RLS-enforcement test
# on POSTGRES_CONN_STRING for exactly this reason (see test_app_role_is_not_bypassrls).
from config.env import POSTGRES_CONN_STRING as _APP_DSN

rls_app_dsn_required = pytest.mark.skipif(
    not _APP_DSN,
    reason="needs the restricted app DSN (POSTGRES_CONN_STRING); superuser engine bypasses RLS",
)


@pytest.fixture(autouse=True)
async def _dispose_shared_engine():
    """Dispose the module-level async engine after each test so a fresh
    pytest-asyncio event loop never reuses a connection bound to a closed loop
    ('Event loop is closed' on the shared engine pool). Mirrors the established
    fixture in test_rls_isolation.py / test_rls_coverage.py."""
    yield
    from shared.db import engine
    await engine.dispose()


@pg_required
@pytest.mark.asyncio
async def test_resolver_includes_custom_role_permissions():
    from sqlalchemy import text
    from shared.db import get_db_session_for_tenant
    from shared.authz.resolver import resolve_permissions_for_user

    tenant = str(_uuid.uuid4())
    user = f"user-{_uuid.uuid4()}"
    role_id = str(_uuid.uuid4())
    ws_id = str(_uuid.uuid4())
    async with get_db_session_for_tenant(tenant) as s:
        await s.execute(text(
            "INSERT INTO organizations (id, slug, display_name) VALUES (:t, :slug, 'T') "
            "ON CONFLICT (id) DO NOTHING"
        ), {"t": tenant, "slug": f"t-{tenant[:8]}"})
        await s.execute(text(
            "INSERT INTO workspaces (id, organization_id, slug, display_name) "
            "VALUES (:w,:t,'default','D')"
        ), {"w": ws_id, "t": tenant})
        await s.execute(text(
            # scope_kind/scope_id are NOT NULL since migration 0004: a custom role now
            # records who owns it. Organization scope with the tenant as scope_id is
            # what a tenant-wide role was before the column existed.
            "INSERT INTO custom_roles (id, tenant_id, name, scope_kind, scope_id) "
            "VALUES (:id,:t,'junior_dev','organization',:t)"
        ), {"id": role_id, "t": tenant})
        await s.execute(text(
            "INSERT INTO custom_role_permissions (id, custom_role_id, permission_name, tenant_id) "
            "VALUES (:id,:rid,'run:create',:t)"
        ), {"id": str(_uuid.uuid4()), "rid": role_id, "t": tenant})
        await s.execute(text(
            "INSERT INTO role_bindings (id, user_id, scope_kind, scope_id, custom_role_id, tenant_id) "
            "VALUES (:id,:u,'business_unit',:w,:rid,:t)"
        ), {"id": str(_uuid.uuid4()), "u": user, "w": ws_id, "rid": role_id, "t": tenant})

    perms = await resolve_permissions_for_user(user, tenant)
    assert "run:create" in perms


def test_custom_roles_router_is_role_manage_gated():
    from shared.routers.custom_roles import custom_roles_router
    routes = [r for r in custom_roles_router.routes if hasattr(r, "dependant")]
    assert routes, "router has no routes"
    for r in routes:
        assert any(
            getattr(getattr(d, "call", None), "__rbac_require_permission__", False)
            for d in r.dependant.dependencies
        ), f"route {r.path} not permission-gated"


def test_custom_role_create_validates_permissions():
    import pytest as _pytest
    from shared.routers.custom_roles import _validate_permissions
    with _pytest.raises(ValueError):
        _validate_permissions(["not:a:real:perm"])
    _validate_permissions(["run:create", "artifact:view"])
    with _pytest.raises(ValueError):
        _validate_permissions(["admin:*"])


@pytest.mark.asyncio
async def test_create_custom_role_duplicate_returns_409(monkeypatch):
    from fastapi import HTTPException
    from sqlalchemy.exc import IntegrityError
    from shared.routers import custom_roles as cr

    class _FakeReq:
        def __init__(self):
            from types import SimpleNamespace
            # admin:* since migration 0004: creating an ORGANIZATION-scoped role now
            # requires org-wide authority, so a caller without it is refused at 403
            # before the path this test is about is ever reached.
            self.state = SimpleNamespace(
                tenant_id="00000000-0000-0000-0000-000000000001",
                user_id="fake-admin",
                permissions=["admin:*"],
            )

    class _FakeDB:
        async def execute(self, *a, **k):
            raise IntegrityError("dup", None, Exception("unique"))

    body = cr.CustomRoleIn(name="dupe", description=None, permissions=[])
    with pytest.raises(HTTPException) as ei:
        await cr.create_custom_role(_FakeReq(), body, db=_FakeDB())
    assert ei.value.status_code == 409


@pytest.mark.asyncio
async def test_create_custom_role_reraises_non_integrity(monkeypatch):
    from fastapi import HTTPException
    from shared.routers import custom_roles as cr

    class _FakeReq:
        def __init__(self):
            from types import SimpleNamespace
            # admin:* since migration 0004: creating an ORGANIZATION-scoped role now
            # requires org-wide authority, so a caller without it is refused at 403
            # before the path this test is about is ever reached.
            self.state = SimpleNamespace(
                tenant_id="00000000-0000-0000-0000-000000000001",
                user_id="fake-admin",
                permissions=["admin:*"],
            )

    class _FakeDB:
        async def execute(self, *a, **k):
            raise RuntimeError("db outage")

    body = cr.CustomRoleIn(name="x", description=None, permissions=[])
    # A non-duplicate error must NOT be masked as 409 — it propagates (not HTTPException 409).
    with pytest.raises(Exception) as ei:
        await cr.create_custom_role(_FakeReq(), body, db=_FakeDB())
    assert not (isinstance(ei.value, HTTPException) and ei.value.status_code == 409)


@pg_required
@pytest.mark.asyncio
async def test_grant_custom_role_assigns_and_resolves():
    from sqlalchemy import text
    from shared.db import get_db_session_for_tenant
    from shared.authz.grant import grant_custom_role
    from shared.authz.resolver import resolve_permissions_for_user

    tenant = str(_uuid.uuid4())
    user = f"user-{_uuid.uuid4()}"
    role_id = str(_uuid.uuid4())
    ws_id = str(_uuid.uuid4())
    async with get_db_session_for_tenant(tenant) as s:
        await s.execute(text(
            "INSERT INTO organizations (id, slug, display_name) VALUES (:t,:slug,'T') "
            "ON CONFLICT (id) DO NOTHING"
        ), {"t": tenant, "slug": f"t2-{tenant[:8]}"})
        await s.execute(text(
            "INSERT INTO workspaces (id, organization_id, slug, display_name) "
            "VALUES (:w,:t,'default','D')"
        ), {"w": ws_id, "t": tenant})
        await s.execute(text(
            "INSERT INTO custom_roles (id, tenant_id, name, scope_kind, scope_id) "
            "VALUES (:id,:t,'qa_plus','organization',:t)"
        ), {"id": role_id, "t": tenant})
        await s.execute(text(
            "INSERT INTO custom_role_permissions (id, custom_role_id, permission_name, tenant_id) "
            "VALUES (:id,:rid,'artifact:export',:t)"
        ), {"id": str(_uuid.uuid4()), "rid": role_id, "t": tenant})

    await grant_custom_role(user, ws_id, role_id, tenant_id=tenant)
    perms = await resolve_permissions_for_user(user, tenant)
    assert "artifact:export" in perms


@rls_app_dsn_required
@pytest.mark.asyncio
async def test_custom_roles_are_tenant_isolated():
    """A custom role created under tenant A is invisible under tenant B (FORCE RLS).

    Connects via the restricted app DSN (POSTGRES_CONN_STRING → sdlc_app, non-BYPASSRLS)
    using the same NullPool + set_config GUC pattern as test_m7_rbac.py. RLS enforcement
    is only observable under a non-BYPASSRLS role; the KV-resolved shared engine uses the
    `postgres` superuser, which bypasses RLS by design and cannot prove isolation.
    """
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    async def _run_with_guc(eng, tid, stmt, params=None):
        async with eng.begin() as conn:
            await conn.execute(
                text("SELECT set_config('app.current_tenant_id', :tid, true)"),
                {"tid": tid},
            )
            result = await conn.execute(stmt, params or {})
            # An INSERT returns no rows; calling fetchall() on it raises
            # ResourceClosedError. Only drain a result that actually has rows.
            return result.fetchall() if result.returns_rows else []

    tenant_a = str(_uuid.uuid4())
    tenant_b = str(_uuid.uuid4())
    role_id = str(_uuid.uuid4())

    eng = create_async_engine(_APP_DSN, poolclass=NullPool)
    try:
        await _run_with_guc(
            eng, tenant_a,
            text(
                "INSERT INTO custom_roles (id, tenant_id, name, scope_kind, scope_id) "
                "VALUES (:id,:t,'secret_role','organization',:t)"
            ),
            {"id": role_id, "t": tenant_a},
        )

        rows_b = await _run_with_guc(
            eng, tenant_b,
            text("SELECT id FROM custom_roles WHERE id = :id"),
            {"id": role_id},
        )
        assert rows_b == [], "RLS leak: tenant B sees tenant A's custom role"

        rows_a = await _run_with_guc(
            eng, tenant_a,
            text("SELECT id FROM custom_roles WHERE id = :id"),
            {"id": role_id},
        )
        assert len(rows_a) == 1
    finally:
        await _run_with_guc(
            eng, tenant_a,
            text("DELETE FROM custom_roles WHERE id = :id"),
            {"id": role_id},
        )
        await eng.dispose()
