"""100K-row RLS performance benchmark for audit_events — milestone-7.1 exit gate (SC-05).

Validates that:
  1. An audit_events query filtered by tenant (via SET LOCAL app.current_tenant_id)
     runs in < 2x the no-RLS baseline latency (SC-05).
  2. EXPLAIN (ANALYZE, BUFFERS) confirms the query uses the composite index
     ix_audit_tenant_created (Index Scan, not Seq Scan) introduced in migration 0006.

Prerequisites (operator must complete before running):
  - Docker compose postgres running.
  - alembic upgrade head applied with superuser POSTGRES_MIGRATIONS_CONN_STRING
    (migrations 0001 through 0006 must be applied).
  - POSTGRES_CONN_STRING set to the NON-BYPASSRLS app role DSN.
  - POSTGRES_MIGRATIONS_CONN_STRING set to the superuser DSN (used here for seeding
    only — bypasses RLS during bulk insert of 100K rows).

Usage:
    cd agentic_app
    PYTHONPATH=. python tests/perf/bench_rls_audit_query.py

Expected output (success):
    [seed]   inserted 100000 audit_events across 2 tenants
    [RLS]    10 iterations, median latency: X.XXX ms
    [no-RLS] 10 iterations, median latency: Y.YYY ms
    [ratio]  RLS / no-RLS = Z.ZZ (must be < 2.0) — PASS
    [EXPLAIN] Index Scan using ix_audit_tenant_created — PASS

Exit code 0 = all assertions passed.
Exit code 1 = at least one assertion failed (ratio >= 2x or Seq Scan detected).
"""
from __future__ import annotations

import asyncio
import os
import statistics
import sys
import time
import uuid
from pathlib import Path

# Allow direct execution: `python tests/perf/bench_rls_audit_query.py`
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from config.env import POSTGRES_CONN_STRING, POSTGRES_MIGRATIONS_CONN_STRING

# ── Constants ──────────────────────────────────────────────────────────────────

_ROWS = 100_000          # total rows to seed
_TENANTS = 2             # distribute across N tenants
_QUERY_LIMIT = 50        # rows per benchmarked query (realistic page size)
_ITERATIONS = 10         # repetitions for stable median
_MAX_RLS_RATIO = 2.0     # SC-05: RLS must be < 2x baseline
_INDEX_NAME = "ix_audit_tenant_created"

# Well-known tenant UUIDs for the benchmark (deterministic, easy to clean up).
_BENCH_TENANTS = [
    uuid.UUID(f"00000000-0000-0000-0099-{i:012d}") for i in range(1, _TENANTS + 1)
]
_BENCH_ORG_IDS = [
    uuid.UUID(f"00000000-0000-0000-0088-{i:012d}") for i in range(1, _TENANTS + 1)
]

# ── Engine factories ───────────────────────────────────────────────────────────


def _make_engine(conn_str: str):
    """Return an async engine for the given connection string."""
    return create_async_engine(conn_str, pool_size=2, max_overflow=2, echo=False)


def _make_session_factory(engine):
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


# ── Seed ──────────────────────────────────────────────────────────────────────


async def seed_rows(migrations_engine) -> None:
    """Insert ROWS audit_events rows across TENANTS tenants using superuser DSN.

    The superuser DSN (POSTGRES_MIGRATIONS_CONN_STRING) bypasses RLS — required
    so that the seed can write rows for all tenants without needing the GUC set.
    Existing benchmark rows are deleted first so the seed is idempotent.
    """
    factory = _make_session_factory(migrations_engine)

    async with factory() as session:
        # Seed prerequisite org rows (audit_events has no FK but organizations
        # must exist as the domain anchor — insert idempotently).
        for i, (org_id, tenant_id) in enumerate(zip(_BENCH_ORG_IDS, _BENCH_TENANTS)):
            await session.execute(
                text("""
                    INSERT INTO organizations (id, slug, display_name, created_at, updated_at)
                    VALUES (:id, :slug, :dn, now(), now())
                    ON CONFLICT (id) DO NOTHING
                """),
                {
                    "id": str(org_id),
                    "slug": f"bench-org-{i + 1}",
                    "dn": f"Bench Org {i + 1}",
                },
            )

        # Remove previous benchmark rows for clean measurement.
        tenant_ids_str = ", ".join(f"'{t}'" for t in _BENCH_TENANTS)
        await session.execute(
            text(
                f"DELETE FROM audit_events WHERE tenant_id IN ({tenant_ids_str})"
            )
        )
        await session.commit()

    # Bulk-insert in chunks to avoid a single huge transaction.
    _CHUNK = 5_000
    rows_per_tenant = _ROWS // _TENANTS
    inserted = 0

    async with factory() as session:
        for tenant_id in _BENCH_TENANTS:
            for chunk_start in range(0, rows_per_tenant, _CHUNK):
                chunk_end = min(chunk_start + _CHUNK, rows_per_tenant)
                count = chunk_end - chunk_start
                # Use a VALUES-list insert via raw SQL for speed (no ORM overhead).
                values_parts = []
                for j in range(count):
                    row_id = uuid.uuid4()
                    values_parts.append(
                        f"('{row_id}', '{tenant_id}', 'bench_event', now() - interval '{j} seconds')"
                    )
                values_sql = ",\n".join(values_parts)
                await session.execute(
                    text(
                        f"INSERT INTO audit_events (id, tenant_id, event_type, created_at) "
                        f"VALUES {values_sql}"
                    )
                )
                await session.commit()
                inserted += count

    print(f"[seed]   inserted {inserted} audit_events across {_TENANTS} tenants")


# ── Query benchmark ────────────────────────────────────────────────────────────


async def _run_query_with_guc(session: AsyncSession, tenant_id: str) -> float:
    """Run the benchmarked query with the tenant GUC set (RLS active path).

    Query shape: filter on tenant via GUC + ORDER BY created_at DESC LIMIT N.
    This is the representative production read that the composite index is for.
    """
    await session.execute(
        text("SELECT set_config('app.current_tenant_id', :tid, true)"),
        {"tid": tenant_id},
    )
    t0 = time.perf_counter()
    await session.execute(
        text(
            "SELECT id, event_type, created_at "
            "FROM audit_events "
            "ORDER BY created_at DESC "
            f"LIMIT {_QUERY_LIMIT}"
        )
    )
    elapsed_ms = (time.perf_counter() - t0) * 1000
    # Reset GUC to avoid bleed across iterations (Pitfall 1).
    await session.execute(text("SET LOCAL app.current_tenant_id = ''"))
    return elapsed_ms


async def _run_query_no_rls(session: AsyncSession, tenant_id: str) -> float:
    """Run the same query shape without the tenant GUC (superuser baseline).

    The superuser session bypasses RLS, so this measures the raw index-scan
    cost without predicate filtering — the no-RLS baseline for the ratio.
    """
    t0 = time.perf_counter()
    await session.execute(
        text(
            f"SELECT id, event_type, created_at "
            f"FROM audit_events "
            f"WHERE tenant_id = '{tenant_id}' "
            f"ORDER BY created_at DESC "
            f"LIMIT {_QUERY_LIMIT}"
        )
    )
    elapsed_ms = (time.perf_counter() - t0) * 1000
    return elapsed_ms


async def benchmark(app_engine, su_engine) -> tuple[float, float]:
    """Run ITERATIONS queries for each path; return (rls_median_ms, baseline_median_ms)."""
    app_factory = _make_session_factory(app_engine)
    su_factory = _make_session_factory(su_engine)
    bench_tenant = str(_BENCH_TENANTS[0])

    rls_times: list[float] = []
    baseline_times: list[float] = []

    # Warm-up (not measured): prime the connection pool and buffer cache.
    async with app_factory() as warm:
        await warm.execute(
            text("SELECT set_config('app.current_tenant_id', :tid, true)"),
            {"tid": bench_tenant},
        )
        await warm.execute(
            text(
                f"SELECT id FROM audit_events ORDER BY created_at DESC LIMIT {_QUERY_LIMIT}"
            )
        )
        await warm.execute(text("SET LOCAL app.current_tenant_id = ''"))

    # RLS path (app role + GUC set).
    for _ in range(_ITERATIONS):
        async with app_factory() as session:
            rls_times.append(await _run_query_with_guc(session, bench_tenant))

    # Baseline path (superuser, explicit WHERE, same query shape).
    for _ in range(_ITERATIONS):
        async with su_factory() as session:
            baseline_times.append(await _run_query_no_rls(session, bench_tenant))

    rls_median = statistics.median(rls_times)
    base_median = statistics.median(baseline_times)
    print(f"[RLS]    {_ITERATIONS} iterations, median latency: {rls_median:.3f} ms")
    print(f"[no-RLS] {_ITERATIONS} iterations, median latency: {base_median:.3f} ms")
    return rls_median, base_median


# ── EXPLAIN verification ───────────────────────────────────────────────────────


async def verify_index_usage(app_engine) -> bool:
    """Run EXPLAIN (ANALYZE, BUFFERS) on the RLS query; assert Index Scan on ix_audit_tenant_created.

    Returns True if the index is confirmed used, False otherwise.
    Prints the relevant plan lines for the operator to inspect.
    """
    app_factory = _make_session_factory(app_engine)
    bench_tenant = str(_BENCH_TENANTS[0])

    async with app_factory() as session:
        await session.execute(
            text("SELECT set_config('app.current_tenant_id', :tid, true)"),
            {"tid": bench_tenant},
        )
        result = await session.execute(
            text(
                "EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT) "
                "SELECT id, event_type, created_at "
                "FROM audit_events "
                "ORDER BY created_at DESC "
                f"LIMIT {_QUERY_LIMIT}"
            )
        )
        plan_lines = [row[0] for row in result.fetchall()]
        await session.execute(text("SET LOCAL app.current_tenant_id = ''"))

    print("\n[EXPLAIN] Query plan (abbreviated):")
    index_line_found = False
    for line in plan_lines:
        if _INDEX_NAME in line or "Index Scan" in line or "Seq Scan" in line or "Bitmap" in line:
            print(f"  {line}")
        if _INDEX_NAME in line and "Index Scan" in line:
            index_line_found = True

    if not index_line_found:
        # Print the full plan so the operator can diagnose.
        print("[EXPLAIN] Full plan for diagnosis:")
        for line in plan_lines:
            print(f"  {line}")

    return index_line_found


# ── Main ───────────────────────────────────────────────────────────────────────


async def main() -> int:
    """Run the full benchmark suite. Returns 0 (pass) or 1 (fail)."""
    if not POSTGRES_CONN_STRING:
        print("ERROR: POSTGRES_CONN_STRING is not set — cannot connect to the app-role DB.")
        return 1
    if not POSTGRES_MIGRATIONS_CONN_STRING:
        print("ERROR: POSTGRES_MIGRATIONS_CONN_STRING is not set — cannot seed rows (superuser required).")
        return 1

    app_engine = _make_engine(POSTGRES_CONN_STRING)
    su_engine = _make_engine(POSTGRES_MIGRATIONS_CONN_STRING)

    passed = True
    try:
        # ── Seed ──────────────────────────────────────────────────────────────
        await seed_rows(su_engine)

        # ── Benchmark ─────────────────────────────────────────────────────────
        rls_ms, base_ms = await benchmark(app_engine, su_engine)

        if base_ms == 0:
            print("[ratio]  baseline is 0 ms — cannot compute ratio (too fast to measure?)")
            ratio = 0.0
        else:
            ratio = rls_ms / base_ms

        ratio_ok = ratio < _MAX_RLS_RATIO
        ratio_label = "PASS" if ratio_ok else f"FAIL (>= {_MAX_RLS_RATIO})"
        print(f"[ratio]  RLS / no-RLS = {ratio:.2f} (must be < {_MAX_RLS_RATIO}) — {ratio_label}")
        if not ratio_ok:
            print(
                f"  ACTION: ratio {ratio:.2f} >= {_MAX_RLS_RATIO}. "
                "Check that migration 0006 created ix_audit_tenant_created and that "
                "autovacuum has run (VACUUM ANALYZE audit_events) to update statistics."
            )
            passed = False

        # ── EXPLAIN / index usage ──────────────────────────────────────────────
        index_used = await verify_index_usage(app_engine)
        index_label = "PASS" if index_used else "FAIL (Seq Scan or index not found in plan)"
        print(f"[EXPLAIN] Index Scan using {_INDEX_NAME} — {index_label}")
        if not index_used:
            print(
                "  ACTION: Index Scan on ix_audit_tenant_created not found. "
                "Run: VACUUM ANALYZE audit_events; then retry. "
                "If still failing, verify CREATE INDEX CONCURRENTLY ran in migration 0006."
            )
            passed = False

    finally:
        await app_engine.dispose()
        await su_engine.dispose()

    if passed:
        print("\n[result] ALL ASSERTIONS PASSED — D-10 exit gate: perf bar met.")
        return 0
    else:
        print("\n[result] ONE OR MORE ASSERTIONS FAILED — see above for actions.")
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
