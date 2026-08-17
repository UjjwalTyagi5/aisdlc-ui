"""Wave-0 RBAC test scaffold — milestone-7.2.

Covers all 8 requirements from the Test Map (RESEARCH §Validation Architecture):
  REQ-M7-09  test_rbac_tables_and_rls           — FORCE RLS on role_bindings (plan 02)
  REQ-M7-10  test_rbac_permission_denied         — developer→approve_requirements = 403 (plan 03)
  REQ-M7-10  test_deny_by_default                — no permissions claim → 403 (plan 03)
  REQ-M7-12  test_permissions_claim_baked        — create_access_token bakes permissions (plan 03)
  D-05       test_route_coverage_guard           — assert_all_routes_protected raises on unprotected route (plan 04)
  D-06       test_default_workspace_multi_raises — resolve_default_workspace raises/409 on >1 workspace (plan 03)
  D-03       test_uwr_cross_tenant_empty         — cross-tenant UWR query returns empty set (plan 02/03)

Tests referencing symbols from plans 02-04 are marked xfail(strict=True) — they fail NOW
and must turn GREEN as those plans land.  Integration tests are skip-guarded on
POSTGRES_CONN_STRING so the CI unit tier stays green before a live DB is available.
"""
from __future__ import annotations

import pytest

from config.env import POSTGRES_CONN_STRING

# ---------------------------------------------------------------------------
# Skip / xfail markers
# ---------------------------------------------------------------------------
_skip_no_db = pytest.mark.skipif(
    not POSTGRES_CONN_STRING,
    reason="POSTGRES_CONN_STRING not set — skipping live-DB RBAC integration tests",
)


# ---------------------------------------------------------------------------
# REQ-M7-09 — RLS flags on role_bindings (plan 02 target)
# ---------------------------------------------------------------------------

@pytest.mark.integration
@_skip_no_db
@pytest.mark.asyncio
async def test_rbac_tables_and_rls():
    """role_bindings must have relrowsecurity AND relforcerowsecurity true in pg_class.
    Global catalog tables (roles, permissions, role_permissions, users) must have relrowsecurity false.

    Verifies that migration 0007 applied the full RLS lifecycle on the new assignment
    table (Pitfall 3) and left the global catalog without RLS (D-03).
    Becomes GREEN when plan 02 migration runs against the live Postgres.
    Skipped when POSTGRES_CONN_STRING is set but DB is not reachable (Docker down).
    """
    from sqlalchemy import text
    from shared.db import AsyncSessionFactory

    try:
        async with AsyncSessionFactory() as session:
            # Check role_bindings has both RLS flags set (Pitfall 3 — ENABLE + FORCE)
            uwr_result = await session.execute(text(
                "SELECT relrowsecurity, relforcerowsecurity "
                "FROM pg_class WHERE relname = 'role_bindings'"
            ))
            uwr_row = uwr_result.one_or_none()

            # Check global catalog tables have NO RLS (D-03)
            catalog_result = await session.execute(text(
                "SELECT relname, relrowsecurity "
                "FROM pg_class "
                "WHERE relname IN ('roles', 'permissions', 'role_permissions', 'users') "
                "ORDER BY relname"
            ))
            catalog_rows = catalog_result.fetchall()
    except Exception as exc:
        pytest.skip(f"DB not reachable or tables absent (migration 0007 not applied?): {exc}")

    assert uwr_row is not None, (
        "role_bindings table not found in pg_class — run migration 0007"
    )
    assert uwr_row[0] is True, (
        "relrowsecurity must be true on role_bindings (ENABLE ROW LEVEL SECURITY not applied)"
    )
    assert uwr_row[1] is True, (
        "relforcerowsecurity must be true on role_bindings (FORCE ROW LEVEL SECURITY not applied)"
    )

    # All four catalog tables must exist and must NOT have RLS (D-03)
    catalog_names = {r[0] for r in catalog_rows}
    expected_catalog = {"roles", "permissions", "role_permissions", "users"}
    assert expected_catalog == catalog_names, (
        f"Catalog tables missing from pg_class: {expected_catalog - catalog_names}"
    )
    for relname, relrowsecurity in catalog_rows:
        assert relrowsecurity is False, (
            f"Global catalog table '{relname}' must NOT have RLS enabled (D-03)"
        )


# ---------------------------------------------------------------------------
# REQ-M7-10 — Permission-denied path (plan 03 target)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_rbac_permission_denied(monkeypatch):
    """A developer permission set must receive 403 for an approve_requirements-protected check.

    Tests require_permission dependency directly with a request-state stub so no HTTP
    stack or live DB is required.  The workspace lookup is monkeypatched to return a
    fixed UUID, isolating the permission check.
    """
    import uuid
    from types import SimpleNamespace
    from fastapi import HTTPException
    from shared.authz import dependency as dep_module
    from shared.authz.dependency import require_permission

    # Monkeypatch workspace resolution to avoid a DB hit. active_workspace_for_request
    # takes (request, tenant_id) — it stashes the resolved workspace on request.state.
    async def _fake_workspace(request, tenant_id: str):
        return uuid.uuid4()

    monkeypatch.setattr(dep_module, "active_workspace_for_request", _fake_workspace)

    dep = require_permission("artifact:approve_requirements")

    # Developer has run:create + artifact:view but NOT approve_requirements.
    state = SimpleNamespace(
        permissions=["run:create", "artifact:view"],
        tenant_id="00000000-0000-0000-0000-000000000001",
        user_id="dev-user",
    )
    request = SimpleNamespace(state=state, path_params={}, scope={"type": "http"})

    with pytest.raises(HTTPException) as exc_info:
        await dep(request)

    assert exc_info.value.status_code == 403, (
        f"Developer must get 403, got {exc_info.value.status_code}"
    )
    assert exc_info.value.detail == "Forbidden", (
        "403 body must be generic 'Forbidden' — no permission-name leak"
    )

    # admin:* must bypass the check.
    admin_state = SimpleNamespace(
        permissions=["admin:*"],
        tenant_id="00000000-0000-0000-0000-000000000001",
        user_id="admin-user",
    )
    admin_request = SimpleNamespace(state=admin_state, path_params={}, scope={"type": "http"})
    # Should not raise.
    await dep(admin_request)


# ---------------------------------------------------------------------------
# REQ-M7-10 — Deny-by-default (no permissions claim → 403) (plan 03 target)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_deny_by_default(monkeypatch):
    """An absent or empty permissions attribute must receive 403 (deny-by-default, REQ-M7-10).

    Tests require_permission dependency directly with a request-state stub so no HTTP
    stack or live DB is required.
    """
    import uuid
    from types import SimpleNamespace
    from fastapi import HTTPException
    from shared.authz import dependency as dep_module
    from shared.authz.dependency import require_permission

    # Monkeypatch workspace resolution to avoid a DB hit. active_workspace_for_request
    # takes (request, tenant_id) — it stashes the resolved workspace on request.state.
    async def _fake_workspace(request, tenant_id: str):
        return uuid.uuid4()

    monkeypatch.setattr(dep_module, "active_workspace_for_request", _fake_workspace)

    dep = require_permission("artifact:approve_requirements")

    # Case 1: no permissions attribute at all (absent from state).
    state_absent = SimpleNamespace(
        tenant_id="00000000-0000-0000-0000-000000000001",
        user_id="no-perms-user",
    )
    request_absent = SimpleNamespace(state=state_absent, path_params={}, scope={"type": "http"})
    with pytest.raises(HTTPException) as exc_info:
        await dep(request_absent)
    assert exc_info.value.status_code == 403

    # Case 2: empty permissions list (deny-by-default).
    state_empty = SimpleNamespace(
        permissions=[],
        tenant_id="00000000-0000-0000-0000-000000000001",
        user_id="no-perms-user",
    )
    request_empty = SimpleNamespace(state=state_empty, path_params={}, scope={"type": "http"})
    with pytest.raises(HTTPException) as exc_info2:
        await dep(request_empty)
    assert exc_info2.value.status_code == 403


# ---------------------------------------------------------------------------
# REQ-M7-12 — permissions claim baked in JWT (plan 03 target)
# ---------------------------------------------------------------------------

def test_permissions_claim_baked():
    """create_access_token with a permissions list must produce a JWT whose decoded payload
    carries that exact permissions array (REQ-M7-12 / D-02).
    """
    import jwt as pyjwt
    from config.env import JWT_SECRET_KEY
    from config.auth.jwt import create_access_token

    perms = ["artifact:view", "artifact:approve_requirements"]
    # The extended signature (permissions param) lands in plan 03.
    token = create_access_token(
        user_id="pm-user",
        tenant_id="00000000-0000-0000-0000-000000000001",
        permissions=perms,
    )
    decoded = pyjwt.decode(token, JWT_SECRET_KEY, algorithms=["HS256"])
    assert decoded.get("permissions") == perms, (
        f"Expected permissions {perms!r} in token payload, got {decoded.get('permissions')!r}"
    )


# ---------------------------------------------------------------------------
# D-05 — route coverage guard raises on unprotected route (plan 04 target)
# ---------------------------------------------------------------------------

def test_route_coverage_guard():
    """assert_all_routes_protected must raise RuntimeError when given an app that has
    at least one route that is neither require_permission-protected nor marked public (D-05).
    """
    from fastapi import FastAPI
    from fastapi.routing import APIRoute

    from shared.authz.dependency import assert_all_routes_protected

    bare_app = FastAPI()

    @bare_app.get("/unprotected-route")
    async def _unprotected():
        return {}

    with pytest.raises(RuntimeError, match="unprotected"):
        assert_all_routes_protected(bare_app)


# ---------------------------------------------------------------------------
# D-06 — resolve_default_workspace raises on >1 workspace (plan 03 target)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_default_workspace_multi_raises(monkeypatch):
    """resolve_default_workspace must raise HTTP 409 when an org has >1 workspace.

    Fail loud, never silently apply the wrong workspace's roles (D-06).  The >1 branch
    is tested by monkeypatching the DB query so no live Postgres is required.
    """
    import uuid
    from fastapi import HTTPException
    from shared.authz import workspace as ws_module
    from shared.authz.workspace import resolve_default_workspace

    # Simulate two workspaces returned for the tenant (the multi-workspace case).
    class _FakeWorkspace:
        def __init__(self, wid):
            self.id = wid

    class _FakeSession:
        async def execute(self, stmt):
            class _Result:
                def scalars(self):
                    class _Scalars:
                        def all(self):
                            return [
                                _FakeWorkspace(uuid.uuid4()),
                                _FakeWorkspace(uuid.uuid4()),
                            ]
                    return _Scalars()
            return _Result()
        async def commit(self): pass
        async def rollback(self): pass

    class _FakeCtx:
        def __init__(self, tid): pass
        async def __aenter__(self): return _FakeSession()
        async def __aexit__(self, *a): pass

    monkeypatch.setattr(ws_module, "get_db_session_for_tenant", _FakeCtx)

    with pytest.raises(HTTPException) as exc_info:
        await resolve_default_workspace(str(uuid.uuid4()))

    assert exc_info.value.status_code == 409, (
        f"Expected HTTP 409 for multi-workspace org, got {exc_info.value.status_code}"
    )


# ---------------------------------------------------------------------------
# D-03 — cross-tenant RoleBinding isolation (plan 02/03 target)
# ---------------------------------------------------------------------------

@pytest.mark.integration
@_skip_no_db
@pytest.mark.asyncio
async def test_uwr_cross_tenant_empty():
    """A RoleBinding query under tenant B's GUC must return zero rows for tenant A's data.

    Verifies that FORCE RLS on role_bindings prevents cross-tenant role-assignment
    leaks (D-03 / Pitfall 3).  Becomes GREEN when migration 0007 and the RLS policy land
    in plan 02.  The seeded row is cleaned up via tenant-A's session in a finally block.
    """
    import uuid
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool as _NullPool

    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    user_id = f"rls-test-user-{uuid.uuid4()}"  # unique per test run to avoid conflicts

    seeded_uwr_id: uuid.UUID | None = None
    test_workspace_id: uuid.UUID | None = None
    test_org_id: uuid.UUID | None = None

    # Use a fresh NullPool engine per test to avoid pytest-asyncio event-loop pool
    # contamination when multiple async tests share the module-level engine.
    # NullPool creates a new physical connection per context-manager open, so no
    # cross-test state leaks between the two integration tests in this file.
    _conn_str = POSTGRES_CONN_STRING or ""

    async def _run_with_guc(engine, tenant_id_str: str, stmt, params=None):
        """Execute stmt under a GUC-scoped transaction — mirrors get_db_session_for_tenant."""
        async with engine.begin() as conn:
            await conn.execute(
                text("SELECT set_config('app.current_tenant_id', :tid, true)"),
                {"tid": tenant_id_str},
            )
            result = await conn.execute(stmt, params or {})
            return result

    # Seed a test org + workspace first so workspace_id FK is satisfied.
    # organizations and workspaces have NO RLS — the GUC is irrelevant for them.
    try:
        _engine = create_async_engine(_conn_str, poolclass=_NullPool)
        async with _engine.begin() as conn:
            test_org_id = uuid.uuid4()
            test_workspace_id = uuid.uuid4()
            await conn.execute(
                text(
                    "INSERT INTO organizations (id, slug, display_name, created_at, updated_at) "
                    "VALUES (:id, :slug, :dn, now(), now())"
                ),
                {"id": str(test_org_id), "slug": f"rls-test-org-{test_org_id}", "dn": "RLS Test Org"},
            )
            await conn.execute(
                text(
                    "INSERT INTO workspaces (id, organization_id, slug, display_name, created_at, updated_at) "
                    "VALUES (:id, :org_id, :slug, :dn, now(), now())"
                ),
                {
                    "id": str(test_workspace_id),
                    "org_id": str(test_org_id),
                    "slug": "rls-test-ws",
                    "dn": "RLS Test Workspace",
                },
            )
        await _engine.dispose()
    except Exception as exc:
        pytest.skip(f"Could not create test org/workspace for UWR seed: {exc}")

    workspace_id = test_workspace_id

    # Seed a UWR row for tenant A under tenant A's GUC (WITH CHECK passes).
    # FORCE RLS means even the app user must satisfy the policy — the GUC must be set.
    try:
        _engine_a = create_async_engine(_conn_str, poolclass=_NullPool)
        seeded_uwr_id = uuid.uuid4()
        await _run_with_guc(
            _engine_a,
            str(tenant_a),
            text(
                "INSERT INTO role_bindings "
                "(id, user_id, scope_kind, scope_id, role_name, tenant_id) "
                "VALUES (:id, :uid, 'business_unit', :wid, :rn, :tid)"
            ),
            {
                "id": str(seeded_uwr_id),
                "uid": user_id,
                "wid": str(workspace_id),
                "rn": "developer",
                "tid": str(tenant_a),
            },
        )
        await _engine_a.dispose()
    except Exception as exc:
        pytest.skip(f"Could not seed RoleBinding (migration 0007 not applied?): {exc}")

    try:
        # Query from tenant B's GUC — RLS must return zero rows (cross-tenant isolation proof).
        _engine_b = create_async_engine(_conn_str, poolclass=_NullPool)
        rows_result = await _run_with_guc(
            _engine_b,
            str(tenant_b),
            text("SELECT id FROM role_bindings WHERE user_id = :uid"),
            {"uid": user_id},
        )
        rows = rows_result.fetchall()
        await _engine_b.dispose()

        assert len(rows) == 0, (
            f"Cross-tenant isolation failure: tenant B session returned {len(rows)} rows "
            f"seeded under tenant A (D-03 / T-7.2-05)"
        )
    finally:
        # Clean up the seeded UWR row via tenant A's GUC — FORCE RLS requires the GUC for DELETE too.
        if seeded_uwr_id is not None:
            try:
                _engine_c = create_async_engine(_conn_str, poolclass=_NullPool)
                await _run_with_guc(
                    _engine_c,
                    str(tenant_a),
                    text("DELETE FROM role_bindings WHERE id = :id"),
                    {"id": str(seeded_uwr_id)},
                )
                await _engine_c.dispose()
            except Exception:
                pass  # cleanup failure should not mask the assertion result

        # Clean up the test workspace and org (non-RLS tables — no GUC needed).
        if test_workspace_id is not None or test_org_id is not None:
            try:
                _engine_d = create_async_engine(_conn_str, poolclass=_NullPool)
                async with _engine_d.begin() as conn:
                    if test_workspace_id is not None:
                        await conn.execute(
                            text("DELETE FROM workspaces WHERE id = :id"),
                            {"id": str(test_workspace_id)},
                        )
                    if test_org_id is not None:
                        await conn.execute(
                            text("DELETE FROM organizations WHERE id = :id"),
                            {"id": str(test_org_id)},
                        )
                await _engine_d.dispose()
            except Exception:
                pass  # cleanup failure should not mask the assertion result


# ---------------------------------------------------------------------------
# D-09 — grant_role bootstrap helper (plan 03 target)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_grant_role_rejects_empty_tenant():
    """grant_role must raise ValueError immediately on an empty tenant_id (fail-closed).

    Unit-level assertion — no DB required.  Ensures the grant helper cannot be called
    without an explicit tenant, mirroring the resolver's fail-closed contract (T-7.2-20).
    """
    import uuid
    from shared.authz.grant import grant_role

    with pytest.raises(ValueError, match="tenant_id is required"):
        await grant_role("u1", uuid.uuid4(), "admin", tenant_id="")


@pytest.mark.asyncio
async def test_grant_role_rejects_unknown_role():
    """grant_role must raise ValueError for a role not in ALL_ROLES."""
    import uuid
    from shared.authz.grant import grant_role

    with pytest.raises(ValueError, match="Unknown role"):
        await grant_role(
            "u1",
            uuid.uuid4(),
            "superadmin_typo",
            tenant_id=str(uuid.uuid4()),
        )


def test_grant_role_uses_tenant_session_not_superuser():
    """grant_role source must import/use get_db_session_for_tenant and never get_db_session_superuser.

    Static analysis test — checks imports to assert the structural security invariant
    without running a live DB (T-7.2-19 / T-7.2-20).
    """
    import ast
    import pathlib

    src = pathlib.Path(__file__).parent.parent / "shared" / "authz" / "grant.py"
    content = src.read_text()

    # Parse AST to check imports, not just string presence (comments may mention forbidden names).
    tree = ast.parse(content)
    imported_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                imported_names.add(alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imported_names.add(alias.name)

    assert "get_db_session_for_tenant" in imported_names, (
        "grant_role must import get_db_session_for_tenant (sets RLS GUC)"
    )
    assert "get_db_session_superuser" not in imported_names, (
        "grant_role must NOT import get_db_session_superuser — FORCE RLS blocks it (Pitfall 5)"
    )
    assert "ON CONFLICT" in content, (
        "grant_role must use ON CONFLICT DO NOTHING for idempotency"
    )
    assert "__main__" in content, (
        "grant_role module must have a CLI entrypoint (python -m ... , D-09)"
    )
    assert "required=True" in content or "required = True" in content, (
        "--tenant-id must be required in argparse (no implicit tenant, T-7.2-20)"
    )


@pytest.mark.integration
@_skip_no_db
@pytest.mark.asyncio
async def test_grant_role_idempotent():
    """grant_role called twice produces exactly one role_bindings row (ON CONFLICT DO NOTHING).

    Integration test — requires POSTGRES_CONN_STRING and migration 0007.
    """
    import uuid
    from sqlalchemy import text as _text
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool as _NullPool
    from shared.authz.grant import grant_role

    tenant_id = str(uuid.uuid4())
    user_id = f"grant-test-user-{uuid.uuid4()}"

    # Seed org + workspace so workspace_id FK is satisfied.
    org_id = uuid.uuid4()
    workspace_id = uuid.uuid4()
    _conn_str = POSTGRES_CONN_STRING or ""
    try:
        _engine = create_async_engine(_conn_str, poolclass=_NullPool)
        async with _engine.begin() as conn:
            await conn.execute(
                _text(
                    "INSERT INTO organizations (id, slug, display_name, created_at, updated_at) "
                    "VALUES (:id, :slug, :dn, now(), now())"
                ),
                {"id": str(org_id), "slug": f"grant-test-org-{org_id}", "dn": "Grant Test Org"},
            )
            await conn.execute(
                _text(
                    "INSERT INTO workspaces (id, organization_id, slug, display_name, created_at, updated_at) "
                    "VALUES (:id, :org_id, :slug, :dn, now(), now())"
                ),
                {"id": str(workspace_id), "org_id": str(org_id), "slug": "grant-test-ws", "dn": "Grant Test WS"},
            )
        await _engine.dispose()
    except Exception as exc:
        pytest.skip(f"Could not seed org/workspace: {exc}")

    try:
        # Call grant_role twice — second call is a no-op (ON CONFLICT DO NOTHING).
        await grant_role(user_id, workspace_id, "developer", tenant_id=tenant_id)
        await grant_role(user_id, workspace_id, "developer", tenant_id=tenant_id)

        # Verify exactly one row exists.
        _engine2 = create_async_engine(_conn_str, poolclass=_NullPool)
        async with _engine2.begin() as conn:
            await conn.execute(
                _text("SELECT set_config('app.current_tenant_id', :tid, true)"),
                {"tid": tenant_id},
            )
            result = await conn.execute(
                _text(
                    "SELECT COUNT(*) FROM role_bindings "
                    "WHERE user_id = :uid AND role_name = 'developer'"
                ),
                {"uid": user_id},
            )
            count = result.scalar()
        await _engine2.dispose()

        assert count == 1, f"Expected 1 UWR row after two idempotent grants, got {count}"
    finally:
        # Cleanup — GUC required for UWR DELETE (FORCE RLS).
        try:
            _ec = create_async_engine(_conn_str, poolclass=_NullPool)
            async with _ec.begin() as conn:
                await conn.execute(
                    _text("SELECT set_config('app.current_tenant_id', :tid, true)"),
                    {"tid": tenant_id},
                )
                await conn.execute(
                    _text("DELETE FROM role_bindings WHERE user_id = :uid"),
                    {"uid": user_id},
                )
            await _ec.dispose()
        except Exception:
            pass
        try:
            _ed = create_async_engine(_conn_str, poolclass=_NullPool)
            async with _ed.begin() as conn:
                await conn.execute(_text("DELETE FROM workspaces WHERE id = :id"), {"id": str(workspace_id)})
                await conn.execute(_text("DELETE FROM organizations WHERE id = :id"), {"id": str(org_id)})
            await _ed.dispose()
        except Exception:
            pass


@pytest.mark.integration
@_skip_no_db
@pytest.mark.asyncio
async def test_grant_role_upserts_user():
    """grant_role creates the User row for a new user_id; re-granting does not duplicate it.

    Integration test — requires POSTGRES_CONN_STRING and migration 0007.
    """
    import uuid
    from sqlalchemy import text as _text
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool as _NullPool
    from shared.authz.grant import grant_role

    tenant_id = str(uuid.uuid4())
    user_id = f"new-user-{uuid.uuid4()}"
    org_id = uuid.uuid4()
    workspace_id = uuid.uuid4()
    _conn_str = POSTGRES_CONN_STRING or ""

    try:
        _engine = create_async_engine(_conn_str, poolclass=_NullPool)
        async with _engine.begin() as conn:
            await conn.execute(
                _text(
                    "INSERT INTO organizations (id, slug, display_name, created_at, updated_at) "
                    "VALUES (:id, :slug, :dn, now(), now())"
                ),
                {"id": str(org_id), "slug": f"upsert-test-org-{org_id}", "dn": "Upsert Test Org"},
            )
            await conn.execute(
                _text(
                    "INSERT INTO workspaces (id, organization_id, slug, display_name, created_at, updated_at) "
                    "VALUES (:id, :org_id, :slug, :dn, now(), now())"
                ),
                {"id": str(workspace_id), "org_id": str(org_id), "slug": "upsert-test-ws", "dn": "Upsert Test WS"},
            )
        await _engine.dispose()
    except Exception as exc:
        pytest.skip(f"Could not seed org/workspace: {exc}")

    try:
        # First grant — should create User row.
        await grant_role(user_id, workspace_id, "developer", tenant_id=tenant_id)

        _engine2 = create_async_engine(_conn_str, poolclass=_NullPool)
        async with _engine2.begin() as conn:
            result = await conn.execute(
                _text("SELECT COUNT(*) FROM users WHERE id = :uid"),
                {"uid": user_id},
            )
            count_after_first = result.scalar()
        await _engine2.dispose()
        assert count_after_first == 1, f"Expected 1 User row after first grant, got {count_after_first}"

        # Second grant — User row must NOT be duplicated (ON CONFLICT DO NOTHING on users.id).
        await grant_role(user_id, workspace_id, "developer", tenant_id=tenant_id)

        _engine3 = create_async_engine(_conn_str, poolclass=_NullPool)
        async with _engine3.begin() as conn:
            result = await conn.execute(
                _text("SELECT COUNT(*) FROM users WHERE id = :uid"),
                {"uid": user_id},
            )
            count_after_second = result.scalar()
        await _engine3.dispose()
        assert count_after_second == 1, f"User row duplicated after second grant: {count_after_second}"
    finally:
        try:
            _ec = create_async_engine(_conn_str, poolclass=_NullPool)
            async with _ec.begin() as conn:
                await conn.execute(
                    _text("SELECT set_config('app.current_tenant_id', :tid, true)"),
                    {"tid": tenant_id},
                )
                await conn.execute(
                    _text("DELETE FROM role_bindings WHERE user_id = :uid"),
                    {"uid": user_id},
                )
            await _ec.dispose()
        except Exception:
            pass
        try:
            _ed = create_async_engine(_conn_str, poolclass=_NullPool)
            async with _ed.begin() as conn:
                await conn.execute(_text("DELETE FROM users WHERE id = :id"), {"id": user_id})
                await conn.execute(_text("DELETE FROM workspaces WHERE id = :id"), {"id": str(workspace_id)})
                await conn.execute(_text("DELETE FROM organizations WHERE id = :id"), {"id": str(org_id)})
            await _ed.dispose()
        except Exception:
            pass


@pytest.mark.integration
@_skip_no_db
@pytest.mark.asyncio
async def test_grant_cross_tenant_isolated():
    """A role granted under tenant A is invisible when queried under tenant B's GUC.

    Proves that grant_role writes only under the supplied tenant and cannot cross tenants
    (FORCE RLS + tenant-scoped session — T-7.2-20).
    """
    import uuid
    from sqlalchemy import text as _text
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool as _NullPool
    from shared.authz.grant import grant_role

    tenant_a = str(uuid.uuid4())
    tenant_b = str(uuid.uuid4())
    user_id = f"cross-tenant-test-{uuid.uuid4()}"
    org_id = uuid.uuid4()
    workspace_id = uuid.uuid4()
    _conn_str = POSTGRES_CONN_STRING or ""

    try:
        _engine = create_async_engine(_conn_str, poolclass=_NullPool)
        async with _engine.begin() as conn:
            await conn.execute(
                _text(
                    "INSERT INTO organizations (id, slug, display_name, created_at, updated_at) "
                    "VALUES (:id, :slug, :dn, now(), now())"
                ),
                {"id": str(org_id), "slug": f"cross-test-org-{org_id}", "dn": "Cross Test Org"},
            )
            await conn.execute(
                _text(
                    "INSERT INTO workspaces (id, organization_id, slug, display_name, created_at, updated_at) "
                    "VALUES (:id, :org_id, :slug, :dn, now(), now())"
                ),
                {"id": str(workspace_id), "org_id": str(org_id), "slug": "cross-test-ws", "dn": "Cross Test WS"},
            )
        await _engine.dispose()
    except Exception as exc:
        pytest.skip(f"Could not seed org/workspace: {exc}")

    try:
        # Grant under tenant A.
        await grant_role(user_id, workspace_id, "developer", tenant_id=tenant_a)

        # Query under tenant B's GUC — must return zero rows (RLS isolation).
        _engine_b = create_async_engine(_conn_str, poolclass=_NullPool)
        async with _engine_b.begin() as conn:
            await conn.execute(
                _text("SELECT set_config('app.current_tenant_id', :tid, true)"),
                {"tid": tenant_b},
            )
            result = await conn.execute(
                _text("SELECT COUNT(*) FROM role_bindings WHERE user_id = :uid"),
                {"uid": user_id},
            )
            count_tenant_b = result.scalar()
        await _engine_b.dispose()

        assert count_tenant_b == 0, (
            f"Cross-tenant isolation failure: tenant B sees {count_tenant_b} rows "
            f"seeded under tenant A (T-7.2-20)"
        )
    finally:
        try:
            _ec = create_async_engine(_conn_str, poolclass=_NullPool)
            async with _ec.begin() as conn:
                await conn.execute(
                    _text("SELECT set_config('app.current_tenant_id', :tid, true)"),
                    {"tid": tenant_a},
                )
                await conn.execute(
                    _text("DELETE FROM role_bindings WHERE user_id = :uid"),
                    {"uid": user_id},
                )
            await _ec.dispose()
        except Exception:
            pass
        try:
            _ed = create_async_engine(_conn_str, poolclass=_NullPool)
            async with _ed.begin() as conn:
                await conn.execute(_text("DELETE FROM users WHERE id = :id"), {"id": user_id})
                await conn.execute(_text("DELETE FROM workspaces WHERE id = :id"), {"id": str(workspace_id)})
                await conn.execute(_text("DELETE FROM organizations WHERE id = :id"), {"id": str(org_id)})
            await _ed.dispose()
        except Exception:
            pass


@pytest.mark.integration
@_skip_no_db
@pytest.mark.asyncio
async def test_grant_then_resolve():
    """After grant_role, resolve_permissions_for_user returns the role's permission set.

    Closes the grant -> login-resolve -> permissions loop (D-09, REQ-M7-12, D-02).
    Integration test — requires POSTGRES_CONN_STRING and migration 0007.
    """
    import uuid
    from sqlalchemy import text as _text
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool as _NullPool
    from shared.authz.grant import grant_role
    from shared.authz.resolver import resolve_permissions_for_user
    from shared.authz.permissions import _ROLE_PERMISSIONS

    tenant_id = str(uuid.uuid4())
    user_id = f"loop-test-user-{uuid.uuid4()}"
    org_id = uuid.uuid4()
    workspace_id = uuid.uuid4()
    _conn_str = POSTGRES_CONN_STRING or ""

    try:
        _engine = create_async_engine(_conn_str, poolclass=_NullPool)
        async with _engine.begin() as conn:
            await conn.execute(
                _text(
                    "INSERT INTO organizations (id, slug, display_name, created_at, updated_at) "
                    "VALUES (:id, :slug, :dn, now(), now())"
                ),
                {"id": str(org_id), "slug": f"loop-test-org-{org_id}", "dn": "Loop Test Org"},
            )
            await conn.execute(
                _text(
                    "INSERT INTO workspaces (id, organization_id, slug, display_name, created_at, updated_at) "
                    "VALUES (:id, :org_id, :slug, :dn, now(), now())"
                ),
                {"id": str(workspace_id), "org_id": str(org_id), "slug": "loop-test-ws", "dn": "Loop Test WS"},
            )
        await _engine.dispose()
    except Exception as exc:
        pytest.skip(f"Could not seed org/workspace: {exc}")

    try:
        # Grant ba — it has approve_requirements + run:create + artifact:view.
        await grant_role(user_id, workspace_id, "ba", tenant_id=tenant_id)

        resolved = await resolve_permissions_for_user(user_id, tenant_id)

        expected = sorted(set(_ROLE_PERMISSIONS["ba"]))
        assert resolved == expected, (
            f"Grant->resolve loop failure: expected {expected!r}, got {resolved!r}"
        )
    finally:
        try:
            _ec = create_async_engine(_conn_str, poolclass=_NullPool)
            async with _ec.begin() as conn:
                await conn.execute(
                    _text("SELECT set_config('app.current_tenant_id', :tid, true)"),
                    {"tid": tenant_id},
                )
                await conn.execute(
                    _text("DELETE FROM role_bindings WHERE user_id = :uid"),
                    {"uid": user_id},
                )
            await _ec.dispose()
        except Exception:
            pass
        try:
            _ed = create_async_engine(_conn_str, poolclass=_NullPool)
            async with _ed.begin() as conn:
                await conn.execute(_text("DELETE FROM users WHERE id = :id"), {"id": user_id})
                await conn.execute(_text("DELETE FROM workspaces WHERE id = :id"), {"id": str(workspace_id)})
                await conn.execute(_text("DELETE FROM organizations WHERE id = :id"), {"id": str(org_id)})
            await _ed.dispose()
        except Exception:
            pass
