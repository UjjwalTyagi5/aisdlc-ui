"""RLS schema coverage tests — milestone-7.1 exit gate (SC-06, REQ-M7-06).

Three tests, all guarded by pytest.mark.skipif when POSTGRES_CONN_STRING is unset:

(a) test_all_tenant_id_tables_have_rls_enabled
    Queries pg_class to assert every table with a tenant_id column has
    relrowsecurity = true.  A single missing table breaks CI immediately
    (Pitfall 4 / T-M7.1-17).

(b) test_app_role_is_not_bypassrls
    Asserts the app DB role does not have SUPERUSER or BYPASSRLS.
    A BYPASSRLS role silently disables all tenant isolation (Pitfall 5 /
    T-M7.1-16).  Must fail if POSTGRES_CONN_STRING points at the migrations
    DSN (superuser) by mistake.

(c) test_migrations_dsn_is_not_app_dsn
    Does NOT require DB connectivity — purely env-var comparison.
    Guards against the common misconfiguration where both DSNs point at the
    same superuser account, which would allow Alembic to run as a BYPASSRLS
    role even after RLS is active (Pitfall 2).
"""
from __future__ import annotations

import os

import pytest

from config.env import POSTGRES_CONN_STRING, POSTGRES_MIGRATIONS_CONN_STRING

_skip_no_db = pytest.mark.skipif(
    not POSTGRES_CONN_STRING,
    reason="POSTGRES_CONN_STRING not set — skipping DB-dependent RLS coverage tests",
)

# Tables that carry a tenant_id column but are DELIBERATELY global (non-RLS), each
# documented at its ORM class in shared/models/orm.py:
#   users:          "lookup by sub before tenant GUC is set (D-08)" — login has to
#                    resolve identity before any tenant context exists to scope by.
#   agent_sessions:  "faithful replacement for the legacy Django agent_session table
#                    ... tenant_id is recorded for future RLS hardening but is not an
#                    RLS anchor (mirrors Django, which had no isolation here)".
# A tenant_id column is a signal this gate uses to FIND candidates, not a promise
# that every one must be RLS-anchored — these two are exceptions by design, not by
# accident, so they're named here rather than silently narrowing the query.
_GLOBAL_TENANT_ID_TABLES = {"users", "agent_sessions"}


@pytest.fixture(autouse=True)
async def _dispose_shared_engine():
    """Dispose the module-level async engine after each test.

    pytest-asyncio (asyncio_mode=auto) gives each test a fresh event loop, but
    shared.db.engine is module-scoped and caches asyncpg connections bound to the
    first test's loop. Without disposal, the next test reuses a connection on a
    closed loop → 'Event loop is closed'. Disposing here keeps each test's pool
    bound to its own loop.
    """
    yield
    from shared.db import engine
    await engine.dispose()


@pytest.mark.asyncio
@_skip_no_db
async def test_all_tenant_id_tables_have_rls_enabled():
    """Assert every table with a tenant_id column has relrowsecurity = true in pg_class.

    Zero rows returned = all tables protected.  Non-empty result = immediate CI
    failure, listing the table names that shipped without RLS (SC-06, Pitfall 4,
    T-M7.1-17 mitigation).
    """
    from sqlalchemy import text

    from shared.db import AsyncSessionFactory

    async with AsyncSessionFactory() as session:
        result = await session.execute(
            text("""
                SELECT t.tablename
                FROM pg_tables t
                JOIN information_schema.columns c
                    ON c.table_name   = t.tablename
                    AND c.table_schema = t.schemaname
                JOIN pg_class pc
                    ON pc.relname = t.tablename
                WHERE t.schemaname  = 'public'
                  AND c.column_name = 'tenant_id'
                  AND pc.relrowsecurity = false
            """)
        )
        unprotected = [row[0] for row in result.fetchall() if row[0] not in _GLOBAL_TENANT_ID_TABLES]

    assert unprotected == [], (
        f"Tables with tenant_id but relrowsecurity=false in pg_class: {unprotected}. "
        "Add ALTER TABLE <name> ENABLE ROW LEVEL SECURITY (and FORCE) to the migration, "
        "or — if this table is deliberately global — add it to _GLOBAL_TENANT_ID_TABLES "
        "above with the same justification its ORM class docstring carries."
    )


@pytest.mark.asyncio
@_skip_no_db
async def test_app_role_is_not_bypassrls():
    """Assert the app DB role connecting via POSTGRES_CONN_STRING is not SUPERUSER or BYPASSRLS.

    A BYPASSRLS (or superuser) app role silently bypasses all RLS policies,
    making tenant isolation structurally ineffective (Pitfall 5 / T-M7.1-16).

    This test MUST be run with POSTGRES_CONN_STRING pointing at the restricted
    app role (not the migrations superuser DSN).  If it fails on a correctly
    configured stack it means the app-role DSN was misconfigured.
    """
    from sqlalchemy import text

    from shared.db import AsyncSessionFactory

    async with AsyncSessionFactory() as session:
        row = (
            await session.execute(
                text(
                    "SELECT usesuper, rolbypassrls "
                    "FROM pg_user "
                    "JOIN pg_roles ON pg_user.usename = pg_roles.rolname "
                    "WHERE pg_user.usename = current_user"
                )
            )
        ).fetchone()

    assert row is not None, "Could not query pg_user/pg_roles for current_user — check DB connectivity"
    assert not row.usesuper, (
        "App DB role must NOT be SUPERUSER. "
        "Set POSTGRES_CONN_STRING to the restricted app role, not the migrations superuser DSN."
    )
    assert not row.rolbypassrls, (
        "App DB role must NOT have BYPASSRLS. "
        "A BYPASSRLS role silently disables all tenant isolation (Pitfall 5). "
        "Create a non-BYPASSRLS app role and point POSTGRES_CONN_STRING at it."
    )


def test_migrations_dsn_is_not_app_dsn():
    """Assert POSTGRES_MIGRATIONS_CONN_STRING != POSTGRES_CONN_STRING (env-only, no DB needed).

    If they are identical, Alembic runs as the same role as the application.
    Once RLS is FORCE-active, the app role cannot run schema migrations
    (Pitfall 2).  Even before FORCE RLS, using the same DSN for migrations
    means the app role has at minimum BYPASSRLS — defeating isolation.
    """
    if not POSTGRES_MIGRATIONS_CONN_STRING:
        pytest.skip("POSTGRES_MIGRATIONS_CONN_STRING not set — skip DSN isolation check")

    assert POSTGRES_MIGRATIONS_CONN_STRING != POSTGRES_CONN_STRING, (
        "POSTGRES_MIGRATIONS_CONN_STRING and POSTGRES_CONN_STRING must be DIFFERENT DSNs. "
        "Migrations must run as the superuser role (POSTGRES_MIGRATIONS_CONN_STRING); "
        "the app role (POSTGRES_CONN_STRING) is subject to RLS and cannot run migrations "
        "once FORCE ROW LEVEL SECURITY is active (Pitfall 2)."
    )
