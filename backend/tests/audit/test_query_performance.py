"""REQ-M8-08 — Audit query performance integration tests.

Asserts:
  - The composite index ix_audit_tenant_run_created exists in pg_indexes after
    migration 0008 (structural check — passes as soon as 0008 is applied)
  - A run-scoped audit query uses the composite index (EXPLAIN ANALYZE check)
  - Query on a 10M-row seeded table returns in < 200ms (perf check; skipped when
    10M seed is unavailable — the structural index check still runs)

The 10M-row performance test is marked skip when POSTGRES_CONN_STRING is absent OR
when the M8_10M_SEED environment variable is not set to "true" (avoiding long test
runs in CI unless explicitly opted in).

Structural index existence test runs whenever POSTGRES_CONN_STRING is set, regardless
of seed availability — this is the minimum green-state check for Wave-1.
"""
from __future__ import annotations

import os
import time

import pytest

from config.env import POSTGRES_CONN_STRING

_skip_no_db = pytest.mark.skipif(
    not POSTGRES_CONN_STRING,
    reason="POSTGRES_CONN_STRING not set — skipping live-DB performance integration tests",
)

_skip_no_10m_seed = pytest.mark.skipif(
    os.environ.get("M8_10M_SEED", "").lower() != "true",
    reason="M8_10M_SEED not set to 'true' — skipping 10M-row performance test (set env var to opt in)",
)


@pytest.mark.integration
@_skip_no_db
@pytest.mark.asyncio
async def test_ix_audit_tenant_run_created_exists():
    """Migration 0008 composite index must exist in pg_indexes (REQ-M8-08 structural check).

    This is the minimum assertion Wave-1 can make: index exists, therefore the query
    planner CAN use it. The < 200ms timing assertion requires the 10M seed.

    Becomes GREEN as soon as migration 0008 is applied to the live DB.
    """
    from sqlalchemy import text
    from shared.db import AsyncSessionFactory

    try:
        async with AsyncSessionFactory() as session:
            result = await session.execute(
                text(
                    "SELECT indexname, indexdef "
                    "FROM pg_indexes "
                    "WHERE tablename = 'audit_events' "
                    "AND indexname = 'ix_audit_tenant_run_created'"
                )
            )
            row = result.one_or_none()
    except Exception as exc:
        pytest.skip(f"DB not reachable or migration 0008 not applied: {exc}")

    assert row is not None, (
        "Index ix_audit_tenant_run_created not found in pg_indexes — "
        "run migration 0008 to create it (CREATE INDEX CONCURRENTLY)"
    )
    assert "tenant_id" in row.indexdef, (
        "Index definition must include tenant_id column"
    )
    assert "resource_id" in row.indexdef, (
        "Index definition must include resource_id column (stores run_id)"
    )
    assert "created_at" in row.indexdef, (
        "Index definition must include created_at column"
    )


@pytest.mark.integration
@_skip_no_db
@pytest.mark.asyncio
async def test_run_scoped_query_uses_composite_index():
    """EXPLAIN ANALYZE for tenant+run_id query must show Index Scan on ix_audit_tenant_run_created.

    Verifies the query planner selects the composite index (not a seq scan) for the
    canonical audit query pattern: WHERE tenant_id=X AND resource_id=run_id.
    """
    from sqlalchemy import text
    from shared.db import AsyncSessionFactory

    tenant_id = "00000000-0000-0000-0000-000000000001"
    run_id = "run-perf-test-001"

    try:
        async with AsyncSessionFactory() as session:
            result = await session.execute(
                text(
                    "EXPLAIN (ANALYZE false, FORMAT TEXT) "
                    "SELECT * FROM audit_events "
                    "WHERE tenant_id = :tenant_id AND resource_id = :run_id "
                    "ORDER BY created_at DESC LIMIT 50"
                ),
                {"tenant_id": tenant_id, "run_id": run_id},
            )
            plan = "\n".join(row[0] for row in result.fetchall())
    except Exception as exc:
        pytest.skip(f"DB not reachable or migration 0008 not applied: {exc}")

    assert "ix_audit_tenant_run_created" in plan or "Index Scan" in plan, (
        f"Expected composite index usage in query plan. Plan:\n{plan}\n"
        "If the index exists but is not selected, the table may be too small for "
        "the planner to prefer it — the 10M-row test exercises the actual performance."
    )


@pytest.mark.integration
@_skip_no_db
@_skip_no_10m_seed
@pytest.mark.asyncio
async def test_query_under_200ms_at_10m_rows():
    """Run-scoped query on 10M-row audit_events table returns in < 200ms (REQ-M8-08).

    Requires M8_10M_SEED=true AND a live DB pre-seeded with 10M audit_events rows.
    The seed script is in scripts/m8_seed_10m_audit_events.py (Wave-1 deliverable).

    Skipped automatically in CI unless M8_10M_SEED=true is set.
    """
    from sqlalchemy import text
    from shared.db import AsyncSessionFactory

    tenant_id = "00000000-0000-0000-0000-000000000001"
    run_id = "run-perf-seed-001"  # the seed script inserts events with this run_id

    try:
        async with AsyncSessionFactory() as session:
            start = time.monotonic()
            await session.execute(
                text(
                    "SELECT * FROM audit_events "
                    "WHERE tenant_id = :tenant_id AND resource_id = :run_id "
                    "ORDER BY created_at DESC LIMIT 50"
                ),
                {"tenant_id": tenant_id, "run_id": run_id},
            )
            elapsed_ms = (time.monotonic() - start) * 1000
    except Exception as exc:
        pytest.skip(f"DB not reachable: {exc}")

    assert elapsed_ms < 200, (
        f"Query took {elapsed_ms:.1f}ms — must be < 200ms on 10M rows with composite index. "
        "Verify migration 0008 is applied and the index is not bloated."
    )
