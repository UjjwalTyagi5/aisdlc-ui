"""_purge_tenants (tests/conftest.py) must refuse to delete the default org even when
a caller's diff wrongly includes it — the second, independent layer behind
.env.test's DSN isolation. See docs/local-setup.md, "Test database", for the incident
this guards against: a diff-based cleanup fixture once misattributed the app's own
recreated default org to a test and deleted it.
"""
import uuid as _uuid

import asyncpg
from sqlalchemy import text

from config.env import DEFAULT_ORG_SLUG
from shared.db import get_db_session_superuser
from tests.conftest import _migrations_dsn, _purge_tenants


async def test_the_default_org_survives_even_when_included_in_the_purge_set():
    # Reuse the default org if one is already there. `slug` is unique, and a previous
    # run of this test (or any boot against this database) leaves one behind — inserting
    # unconditionally failed on the constraint rather than on the behaviour under test.
    async with get_db_session_superuser() as s:
        existing = (await s.execute(
            text("SELECT id FROM organizations WHERE slug = :s"), {"s": DEFAULT_ORG_SLUG},
        )).scalar()
        if existing is None:
            real_org = str(_uuid.uuid4())
            await s.execute(
                text("INSERT INTO organizations (id, slug, display_name) VALUES (:i, :s, 'PWC')"),
                {"i": real_org, "s": DEFAULT_ORG_SLUG},
            )
            created = True
        else:
            real_org, created = str(existing), False

    dsn = _migrations_dsn()
    assert dsn is not None, "POSTGRES_MIGRATIONS_CONN_STRING must be set for this test"
    conn = await asyncpg.connect(dsn)
    try:
        await _purge_tenants(conn, {real_org})
        still_there = await conn.fetchval(
            "SELECT 1 FROM organizations WHERE id = $1", _uuid.UUID(real_org)
        )
        assert still_there == 1
    finally:
        if created:
            await conn.execute("DELETE FROM organizations WHERE id = $1", _uuid.UUID(real_org))
        await conn.close()


async def test_a_genuine_non_default_org_is_still_purged_normally():
    other_org = str(_uuid.uuid4())
    async with get_db_session_superuser() as s:
        await s.execute(
            text(
                "INSERT INTO organizations (id, slug, display_name) VALUES (:i, :s, 'Throwaway')"
            ),
            {"i": other_org, "s": f"throwaway-{other_org[:8]}"},
        )

    conn = await asyncpg.connect(_migrations_dsn())
    try:
        await _purge_tenants(conn, {other_org})
        gone = await conn.fetchval(
            "SELECT 1 FROM organizations WHERE id = $1", _uuid.UUID(other_org)
        )
        assert gone is None
    finally:
        await conn.close()
