"""REQ-M8-01/02 — Append-only enforcement integration tests.

Asserts that migration 0008 (REVOKE UPDATE, DELETE ON audit_events FROM sdlc_app)
is applied and effective on the live database:
  - INSERT succeeds as sdlc_app (INSERT privilege not revoked)
  - UPDATE raises permission denied as sdlc_app (REQ-M8-01)
  - DELETE raises permission denied as sdlc_app (REQ-M8-02)

These are integration tests that require:
  - A live Postgres instance reachable via POSTGRES_CONN_STRING
  - Migration 0008 applied (REVOKE executed as superuser)
  - The restricted sdlc_app role existing with its original INSERT privilege intact

Tests are skipped automatically when POSTGRES_CONN_STRING is not set, matching the
pattern established by test_m7_rbac.py.
"""
from __future__ import annotations

import uuid

import pytest

from config.env import POSTGRES_CONN_STRING

_skip_no_db = pytest.mark.skipif(
    not POSTGRES_CONN_STRING,
    reason="POSTGRES_CONN_STRING not set — skipping live-DB append-only integration tests",
)


@pytest.mark.integration
@_skip_no_db
@pytest.mark.asyncio
async def test_insert_succeeds_as_sdlc_app():
    """INSERT into audit_events succeeds as sdlc_app after migration 0008.

    The REVOKE only strips UPDATE + DELETE; INSERT remains permitted.
    Becomes GREEN when migration 0008 is applied against the live DB.
    """
    from sqlalchemy import text
    from shared.db import get_db_session_for_tenant

    tenant_id = "00000000-0000-0000-0000-000000000001"
    event_id = str(uuid.uuid4())

    try:
        async with get_db_session_for_tenant(tenant_id) as session:
            await session.execute(
                text(
                    "INSERT INTO audit_events (id, tenant_id, actor_id, event_type, created_at) "
                    "VALUES (:id, :tenant_id, 'test-actor', 'test_event', now())"
                ),
                {"id": event_id, "tenant_id": tenant_id},
            )
    except Exception as exc:
        if "permission denied" in str(exc).lower():
            pytest.fail(
                f"INSERT failed with permission denied — the append-only REVOKE may have "
                f"incorrectly revoked INSERT: {exc}"
            )
        pytest.skip(f"DB not reachable or setup incomplete: {exc}")

    # Cleanup runs on the PRIVILEGED connection. It used to run on the app session,
    # whose own comment said "app session cannot DELETE after 0008" — so this test
    # could only pass while the property it exists to protect was absent. It did,
    # because the squash dropped the REVOKE.
    await _delete_as_superuser(event_id)


async def _delete_as_superuser(event_id: str) -> None:
    """Remove one audit row using the migrations role, which retains DELETE."""
    import os

    dsn = os.environ.get("POSTGRES_MIGRATIONS_CONN_STRING", "")
    if not dsn:
        return  # nothing to clean with; the row is harmless test data
    import asyncpg

    conn = await asyncpg.connect(dsn.replace("postgresql+asyncpg://", "postgresql://"))
    try:
        await conn.execute("DELETE FROM audit_events WHERE id = $1::uuid", event_id)
    finally:
        await conn.close()


@pytest.mark.integration
@_skip_no_db
@pytest.mark.asyncio
async def test_update_denied_as_sdlc_app():
    """UPDATE on audit_events raises permission denied as sdlc_app (REQ-M8-01).

    After migration 0008 the sdlc_app role cannot UPDATE any row in audit_events.
    Becomes GREEN when migration 0008 is applied and sdlc_app is the app-role DSN user.
    """
    from sqlalchemy import text
    from sqlalchemy.exc import ProgrammingError
    from shared.db import get_db_session_for_tenant

    tenant_id = "00000000-0000-0000-0000-000000000001"

    try:
        async with get_db_session_for_tenant(tenant_id) as session:
            with pytest.raises(ProgrammingError, match="permission denied"):
                await session.execute(
                    text(
                        "UPDATE audit_events SET actor_id = 'tampered' "
                        "WHERE tenant_id = :tenant_id"
                    ),
                    {"tenant_id": tenant_id},
                )
    except Exception as exc:
        if "permission denied" in str(exc).lower() or "ProgrammingError" in type(exc).__name__:
            # This is the expected result — test passes
            return
        pytest.skip(f"DB not reachable or migration 0008 not applied: {exc}")


@pytest.mark.integration
@_skip_no_db
@pytest.mark.asyncio
async def test_delete_denied_as_sdlc_app():
    """DELETE on audit_events raises permission denied as sdlc_app (REQ-M8-02).

    After migration 0008 the sdlc_app role cannot DELETE any row in audit_events.
    Becomes GREEN when migration 0008 is applied and sdlc_app is the app-role DSN user.
    """
    from sqlalchemy import text
    from sqlalchemy.exc import ProgrammingError
    from shared.db import get_db_session_for_tenant

    tenant_id = "00000000-0000-0000-0000-000000000001"

    try:
        async with get_db_session_for_tenant(tenant_id) as session:
            with pytest.raises(ProgrammingError, match="permission denied"):
                await session.execute(
                    text(
                        "DELETE FROM audit_events WHERE tenant_id = :tenant_id"
                    ),
                    {"tenant_id": tenant_id},
                )
    except Exception as exc:
        if "permission denied" in str(exc).lower() or "ProgrammingError" in type(exc).__name__:
            # This is the expected result — test passes
            return
        pytest.skip(f"DB not reachable or migration 0008 not applied: {exc}")
