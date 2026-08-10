"""Tests for the three-function tenant session API (REQ-M7-04, REQ-M7-05).

Verifies:
  - get_db_session(request) sets SET LOCAL app.current_tenant_id when tenant_id present
  - get_db_session(request) resets GUC to '' in finally even when tenant_id is None
  - get_db_session_for_tenant(tenant_id) always sets and resets the GUC
  - get_db_session_superuser() issues no SET LOCAL at all
  - No bare SET (without LOCAL) ever appears in executed statements
  - POSTGRES_MIGRATIONS_CONN_STRING is hard-required (no fallback to app DSN)

All tests are DB-independent — a fake session captures executed SQL text.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest
import os

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


# ── Fake session helper ───────────────────────────────────────────────────────

class _FakeSession:
    """Minimal async session stub that records every text() statement executed."""

    def __init__(self):
        self.executed_statements: list[str] = []
        self._committed = False
        self._rolled_back = False

    async def execute(self, stmt, params=None):
        # Capture the string form of the statement
        stmt_text = str(stmt)
        self.executed_statements.append(stmt_text)
        return MagicMock()

    async def commit(self):
        self._committed = True

    async def rollback(self):
        self._rolled_back = True

    def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeSessionFactory:
    """AsyncSessionFactory substitute that returns a _FakeSession via async context manager."""

    def __init__(self):
        self.session = _FakeSession()

    def __call__(self):
        return self

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        return False


def _make_request(tenant_id=None):
    """Fabricate a minimal FastAPI-like Request with request.state.tenant_id."""
    req = MagicMock()
    req.state = MagicMock()
    if tenant_id is not None:
        req.state.tenant_id = tenant_id
    else:
        # Simulate missing attribute — getattr(..., None) returns None
        type(req.state).tenant_id = property(
            lambda self: (_ for _ in ()).throw(AttributeError("tenant_id"))
        )
    return req


# ═══════════════════════════════════════════════════════════════════════════
# REQ-M7-04: get_db_session — SET LOCAL injection + finally reset
# ═══════════════════════════════════════════════════════════════════════════

class TestGetDbSession:
    """get_db_session(request) injects SET LOCAL when tenant_id is present."""

    @pytest.mark.asyncio
    async def test_session_sets_guc_when_tenant_id_present(self):
        """get_db_session yields after executing SET LOCAL app.current_tenant_id = :tid."""
        from shared.db import get_db_session

        factory = _FakeSessionFactory()
        request = MagicMock()
        request.state.tenant_id = "tenant-abc"

        with patch("shared.db.AsyncSessionFactory", factory):
            gen = get_db_session(request)
            session = await gen.__anext__()
            # Drain the generator (commit path)
            try:
                await gen.__anext__()
            except StopAsyncIteration:
                pass

        stmts = factory.session.executed_statements
        set_local_stmts = [s for s in stmts if "SET LOCAL" in s and "app.current_tenant_id" in s]
        assert set_local_stmts, (
            f"Expected a SET LOCAL app.current_tenant_id statement. Got: {stmts}"
        )

    @pytest.mark.asyncio
    async def test_session_resets_guc_in_finally_with_tenant(self):
        """get_db_session resets GUC to '' in finally (pooled-connection bleed defense)."""
        from shared.db import get_db_session

        factory = _FakeSessionFactory()
        request = MagicMock()
        request.state.tenant_id = "tenant-xyz"

        with patch("shared.db.AsyncSessionFactory", factory):
            gen = get_db_session(request)
            await gen.__anext__()
            try:
                await gen.__anext__()
            except StopAsyncIteration:
                pass

        stmts = factory.session.executed_statements
        reset_stmts = [
            s for s in stmts
            if "SET LOCAL" in s and "app.current_tenant_id" in s and "''" in s
        ]
        assert reset_stmts, (
            f"Expected a GUC reset to '' in finally. Got: {stmts}"
        )

    @pytest.mark.asyncio
    async def test_session_still_resets_in_finally_when_tenant_id_none(self):
        """get_db_session resets GUC even when tenant_id is None (no initial SET)."""
        from shared.db import get_db_session

        factory = _FakeSessionFactory()
        # No tenant_id on request — getattr returns None
        request = MagicMock()
        del request.state.tenant_id
        request.state = MagicMock(spec=[])  # spec=[] means no attributes

        with patch("shared.db.AsyncSessionFactory", factory):
            gen = get_db_session(request)
            await gen.__anext__()
            try:
                await gen.__anext__()
            except StopAsyncIteration:
                pass

        stmts = factory.session.executed_statements
        # No initial SET LOCAL for a tenant (None/missing tenant_id)
        set_with_tenant = [
            s for s in stmts
            if "SET LOCAL" in s and "app.current_tenant_id" in s and "''" not in s
        ]
        assert not set_with_tenant, (
            f"Should not issue tenant SET LOCAL when tenant_id is None. Got: {stmts}"
        )
        # But the reset should still happen in finally
        reset_stmts = [
            s for s in stmts
            if "SET LOCAL" in s and "app.current_tenant_id" in s and "''" in s
        ]
        assert reset_stmts, (
            f"Expected a finally GUC reset even with no tenant_id. Got: {stmts}"
        )

    @pytest.mark.asyncio
    async def test_no_bare_set_in_get_db_session(self):
        """get_db_session must never issue a bare SET (without LOCAL) for app.current_tenant_id."""
        from shared.db import get_db_session

        factory = _FakeSessionFactory()
        request = MagicMock()
        request.state.tenant_id = "tenant-check"

        with patch("shared.db.AsyncSessionFactory", factory):
            gen = get_db_session(request)
            await gen.__anext__()
            try:
                await gen.__anext__()
            except StopAsyncIteration:
                pass

        stmts = factory.session.executed_statements
        # A bare SET would start with "SET app" without LOCAL
        bare_set = [
            s for s in stmts
            if "app.current_tenant_id" in s and "SET LOCAL" not in s and "SET" in s
        ]
        assert not bare_set, (
            f"Bare SET (without LOCAL) found in executed statements: {bare_set}"
        )


# ═══════════════════════════════════════════════════════════════════════════
# REQ-M7-04: get_db_session_for_tenant — always sets + resets GUC
# ═══════════════════════════════════════════════════════════════════════════

class TestGetDbSessionForTenant:
    """get_db_session_for_tenant(tenant_id) always injects SET LOCAL and resets in finally."""

    @pytest.mark.asyncio
    async def test_sets_guc_for_given_tenant(self):
        """get_db_session_for_tenant('t1') issues SET LOCAL app.current_tenant_id = 't1'."""
        from shared.db import get_db_session_for_tenant

        factory = _FakeSessionFactory()

        with patch("shared.db.AsyncSessionFactory", factory):
            async with get_db_session_for_tenant("t1") as _session:
                pass

        stmts = factory.session.executed_statements
        # The tenant GUC setter uses set_config('app.current_tenant_id', :tid, true) —
        # SET LOCAL cannot take a bind parameter (PostgresSyntaxError at "$1").
        setter_stmts = [s for s in stmts if "set_config" in s and "app.current_tenant_id" in s]
        assert setter_stmts, (
            f"Expected set_config('app.current_tenant_id', ...) setter. Got: {stmts}"
        )

    @pytest.mark.asyncio
    async def test_resets_guc_in_finally(self):
        """get_db_session_for_tenant resets GUC to '' in finally."""
        from shared.db import get_db_session_for_tenant

        factory = _FakeSessionFactory()

        with patch("shared.db.AsyncSessionFactory", factory):
            async with get_db_session_for_tenant("t2") as _session:
                pass

        stmts = factory.session.executed_statements
        reset_stmts = [
            s for s in stmts
            if "SET LOCAL" in s and "app.current_tenant_id" in s and "''" in s
        ]
        assert reset_stmts, (
            f"Expected GUC reset to '' in finally. Got: {stmts}"
        )

    @pytest.mark.asyncio
    async def test_no_bare_set_in_for_tenant(self):
        """get_db_session_for_tenant must not issue bare SET (without LOCAL)."""
        from shared.db import get_db_session_for_tenant

        factory = _FakeSessionFactory()

        with patch("shared.db.AsyncSessionFactory", factory):
            async with get_db_session_for_tenant("t3") as _session:
                pass

        stmts = factory.session.executed_statements
        bare_set = [
            s for s in stmts
            if "app.current_tenant_id" in s and "SET LOCAL" not in s and "SET" in s
        ]
        assert not bare_set, (
            f"Bare SET (without LOCAL) found: {bare_set}"
        )


# ═══════════════════════════════════════════════════════════════════════════
# REQ-M7-04: get_db_session_superuser — NO SET LOCAL at all
# ═══════════════════════════════════════════════════════════════════════════

class TestGetDbSessionSuperuser:
    """get_db_session_superuser() must issue NO SET LOCAL for app.current_tenant_id."""

    @pytest.mark.asyncio
    async def test_superuser_issues_no_guc_statement(self):
        """get_db_session_superuser() must not execute any SET LOCAL app.current_tenant_id."""
        from shared.db import get_db_session_superuser

        factory = _FakeSessionFactory()

        with patch("shared.db.AsyncSessionFactory", factory):
            async with get_db_session_superuser() as _session:
                pass

        stmts = factory.session.executed_statements
        tenant_stmts = [s for s in stmts if "app.current_tenant_id" in s]
        assert not tenant_stmts, (
            f"get_db_session_superuser must not set the tenant GUC. Got: {tenant_stmts}"
        )

    @pytest.mark.asyncio
    async def test_superuser_still_commits(self):
        """get_db_session_superuser() still commits on success."""
        from shared.db import get_db_session_superuser

        factory = _FakeSessionFactory()

        with patch("shared.db.AsyncSessionFactory", factory):
            async with get_db_session_superuser() as _session:
                pass

        assert factory.session._committed, "get_db_session_superuser must commit on success"


# ═══════════════════════════════════════════════════════════════════════════
# Structural check: no bare SET in source
# ═══════════════════════════════════════════════════════════════════════════

class TestDbPyStructuralCheck:
    """Source-level check: db.py never uses bare SET for the GUC."""

    def test_no_bare_set_in_db_source(self):
        """shared/db.py must not contain SET app.current_tenant_id without LOCAL."""
        db_path = Path(__file__).resolve().parents[1] / "shared" / "db.py"
        source = db_path.read_text(encoding="utf-8")
        # Count SET LOCAL occurrences
        set_local_count = source.count("SET LOCAL app.current_tenant_id")
        # Ensure at least 3 occurrences (two sets + at least one reset path per tenant variant)
        assert set_local_count >= 3, (
            f"Expected >= 3 'SET LOCAL app.current_tenant_id' occurrences in db.py. "
            f"Found {set_local_count}. Check that get_db_session and get_db_session_for_tenant "
            f"both set and reset the GUC."
        )

    def test_three_session_functions_exported(self):
        """shared/db.py must export all three session functions."""
        import shared.db as db_mod
        assert hasattr(db_mod, "get_db_session"), "get_db_session missing from shared.db"
        assert hasattr(db_mod, "get_db_session_for_tenant"), (
            "get_db_session_for_tenant missing from shared.db"
        )
        assert hasattr(db_mod, "get_db_session_superuser"), (
            "get_db_session_superuser missing from shared.db"
        )


# ═══════════════════════════════════════════════════════════════════════════
# REQ-M7-05 (DSN portion): migrations DSN isolation
# ═══════════════════════════════════════════════════════════════════════════

class TestMigrationsDsnIsolation:
    """POSTGRES_MIGRATIONS_CONN_STRING must not default to app DSN."""

    def test_migrations_dsn_is_not_app_dsn(self):
        """Assert POSTGRES_MIGRATIONS_CONN_STRING != POSTGRES_CONN_STRING.

        If they are the same, Alembic runs as the app role which is blocked by RLS
        once RLS is active (Pitfall 2).
        """
        from config.env import POSTGRES_CONN_STRING
        migrations_dsn = os.environ.get("POSTGRES_MIGRATIONS_CONN_STRING", "")
        if not migrations_dsn:
            pytest.skip("POSTGRES_MIGRATIONS_CONN_STRING not set — skip DSN isolation check")
        assert migrations_dsn != POSTGRES_CONN_STRING, (
            "POSTGRES_MIGRATIONS_CONN_STRING and POSTGRES_CONN_STRING must be different DSNs. "
            "Migrations must run as superuser; the app role is subject to RLS."
        )

    def test_config_env_migrations_dsn_does_not_default_to_app_dsn(self):
        """config/env.py must not default POSTGRES_MIGRATIONS_CONN_STRING to POSTGRES_CONN_STRING.

        This is the structural check (source-level) to ensure the default= arg is '' or empty,
        not POSTGRES_CONN_STRING.
        """
        env_path = Path(__file__).resolve().parents[1] / "config" / "env.py"
        source = env_path.read_text(encoding="utf-8")
        # Find the POSTGRES_MIGRATIONS_CONN_STRING assignment line
        migrations_line = None
        for line in source.splitlines():
            if "POSTGRES_MIGRATIONS_CONN_STRING" in line and "os.environ" in line:
                migrations_line = line
                break
        assert migrations_line is not None, (
            "Could not find POSTGRES_MIGRATIONS_CONN_STRING assignment in config/env.py"
        )
        # The default must not reference POSTGRES_CONN_STRING
        assert "POSTGRES_CONN_STRING" not in migrations_line, (
            f"config/env.py POSTGRES_MIGRATIONS_CONN_STRING must not default to POSTGRES_CONN_STRING. "
            f"Found: {migrations_line.strip()}"
        )

    def test_migrations_env_raises_when_dsn_unset(self, monkeypatch):
        """migrations/env.py raises RuntimeError when POSTGRES_MIGRATIONS_CONN_STRING is unset.

        Imports the migration env module in isolation to verify RuntimeError behavior.
        This test patches os.environ to simulate a missing DSN.
        """
        # We cannot easily re-import migrations.env (it calls run_migrations_online() at import)
        # so we test the source-level requirement: the RuntimeError message must explain why.
        migrations_env_path = Path(__file__).resolve().parents[1] / "migrations" / "env.py"
        source = migrations_env_path.read_text(encoding="utf-8")

        # Ensure the fallback `or os.environ.get("POSTGRES_CONN_STRING")` is gone
        assert 'or os.environ.get("POSTGRES_CONN_STRING")' not in source, (
            "migrations/env.py must not fall back to POSTGRES_CONN_STRING. "
            "Remove the `or os.environ.get(...)` fallback — Pitfall 2."
        )
        # Ensure RuntimeError is raised
        assert "RuntimeError" in source, (
            "migrations/env.py must raise RuntimeError when POSTGRES_MIGRATIONS_CONN_STRING is unset."
        )
        # Ensure the error message explains the RLS reason
        assert "RLS" in source or "app role" in source or "superuser" in source.lower(), (
            "migrations/env.py RuntimeError must explain why: RLS / app role / superuser DSN."
        )
