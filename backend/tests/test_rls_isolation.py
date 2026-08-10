"""Cross-tenant RLS isolation integration test — milestone-7.1 exit gate (SC-01, REQ-M7-08).

Proves that with FORCE ROW LEVEL SECURITY active (migration 0006):
  - A session scoped to tenant-A sees only tenant-A rows.
  - A session scoped to tenant-B returns an EMPTY set — NOT a 403, NOT an error.
    RLS filters silently; no exception is raised (T-M7.1-15 mitigation).
  - A second tenant-A session after the tenant-B read still sees tenant-A rows
    (confirms no GUC bleed from the pool — Pitfall 1).

Design:
  - Uses three canonical tables: runs, artifacts, audit_events (the primary
    cross-tenant read surfaces, per M7 SC-01).
  - Inserts rows under a tenant-A session (get_db_session_for_tenant), so the
    INSERT WITH CHECK policy is satisfied (tenant_id = app.current_tenant_id).
    Note: get_db_session_superuser() does NOT bypass RLS unless the connecting
    DB role is BYPASSRLS/superuser — the production app role is restricted, so
    seeding must go through the tenant GUC, not a "superuser" session.
  - Reads back as tenant-A — expects non-empty.
  - Reads back as tenant-B — expects empty (filtered by RLS, not an error).
  - Cleans up using a tenant-A session in a finally block so no test
    pollution crosses into other suites.

The test uses two well-known UUIDs that are deterministic (never random) so
the cleanup WHERE clause is precise.

Skips gracefully when POSTGRES_CONN_STRING is unset (local dev without DB).
"""
from __future__ import annotations

import uuid

import pytest

from config.env import POSTGRES_CONN_STRING

# Well-known deterministic tenant UUIDs for this test only.
# Chosen outside the E2E range (00000000-e2e0-*) and the legacy range (…0001).
_TENANT_A = uuid.UUID("00000000-0000-0000-0001-000000000001")
_TENANT_B = uuid.UUID("00000000-0000-0000-0002-000000000002")

_skip_no_db = pytest.mark.skipif(
    not POSTGRES_CONN_STRING,
    reason="POSTGRES_CONN_STRING not set — skipping DB-dependent RLS isolation tests",
)


@pytest.fixture(autouse=True)
async def _dispose_shared_engine():
    """Dispose the module-level async engine after each test so a fresh
    pytest-asyncio event loop never reuses a connection bound to a closed loop
    ('Event loop is closed' on the shared engine pool)."""
    yield
    from shared.db import engine
    await engine.dispose()


@pytest.mark.asyncio
@_skip_no_db
async def test_cross_tenant_isolation():
    """Prove tenant-A sees only tenant-A rows; tenant-B gets an empty set (not 403).

    Covers runs, artifacts, and audit_events — the three primary cross-tenant
    read surfaces listed in M7 SC-01.

    The assertion that tenant-B gets zero rows (not an exception) is the
    structural proof that FORCE RLS is active and the GUC setter is working:
    RLS filters silently.
    """
    from sqlalchemy import text

    from shared.db import get_db_session_for_tenant

    # Deterministic IDs for cleanup precision
    run_id_a = uuid.UUID("00000000-1234-0000-0001-000000000001")
    artifact_id_a = uuid.UUID("00000000-1234-0000-0001-000000000002")
    audit_id_a = uuid.UUID("00000000-1234-0000-0001-000000000003")

    # Resolve/create the prerequisite org + workspace + project for tenant-A.
    # The FK chain is organizations → workspaces → projects → runs → artifacts.
    # audit_events only needs tenant_id (no FK to runs by design).
    org_a_id = uuid.UUID("00000000-1234-0000-0001-000000000010")
    ws_a_id = uuid.UUID("00000000-1234-0000-0001-000000000011")
    proj_a_id = uuid.UUID("00000000-1234-0000-0001-000000000012")

    try:
        # ── SETUP: insert under a tenant-A session so the INSERT WITH CHECK policy
        # (tenant_id = app.current_tenant_id) is satisfied. organizations/workspaces
        # have no RLS, so the GUC is simply ignored for those two inserts. ──
        async with get_db_session_for_tenant(str(_TENANT_A)) as su_session:
            # Seed org / workspace / project for tenant-A (idempotent ON CONFLICT).
            await su_session.execute(
                text("""
                    INSERT INTO organizations (id, slug, display_name, created_at, updated_at)
                    VALUES (:id, :slug, :dn, now(), now())
                    ON CONFLICT (id) DO NOTHING
                """),
                {"id": str(org_a_id), "slug": "rls-test-org-a", "dn": "RLS Test Org A"},
            )
            await su_session.execute(
                text("""
                    INSERT INTO workspaces (id, organization_id, slug, display_name, created_at, updated_at)
                    VALUES (:id, :org_id, :slug, :dn, now(), now())
                    ON CONFLICT (id) DO NOTHING
                """),
                {
                    "id": str(ws_a_id),
                    "org_id": str(org_a_id),
                    "slug": "rls-test-ws-a",
                    "dn": "RLS Test WS A",
                },
            )
            await su_session.execute(
                text("""
                    INSERT INTO projects (id, workspace_id, tenant_id, display_name,
                                         provider_kind, archived, created_at, updated_at)
                    VALUES (:id, :ws_id, :tid, :dn, 'azure_devops', false, now(), now())
                    ON CONFLICT (id) DO NOTHING
                """),
                {
                    "id": str(proj_a_id),
                    "ws_id": str(ws_a_id),
                    "tid": str(_TENANT_A),
                    "dn": "RLS Test Project A",
                },
            )
            # Insert the Run for tenant-A
            await su_session.execute(
                text("""
                    INSERT INTO runs (id, project_id, tenant_id, stage, status,
                                      trigger, created_at, updated_at)
                    VALUES (:id, :proj_id, :tid, 'requirements', 'pending',
                            'manual', now(), now())
                    ON CONFLICT (id) DO NOTHING
                """),
                {
                    "id": str(run_id_a),
                    "proj_id": str(proj_a_id),
                    "tid": str(_TENANT_A),
                },
            )
            # Insert the Artifact for tenant-A
            await su_session.execute(
                text("""
                    INSERT INTO artifacts (id, run_id, tenant_id, artifact_type, created_at)
                    VALUES (:id, :run_id, :tid, 'requirements', now())
                    ON CONFLICT (id) DO NOTHING
                """),
                {
                    "id": str(artifact_id_a),
                    "run_id": str(run_id_a),
                    "tid": str(_TENANT_A),
                },
            )
            # Insert the AuditEvent for tenant-A (no FK to run)
            await su_session.execute(
                text("""
                    INSERT INTO audit_events (id, tenant_id, event_type, created_at)
                    VALUES (:id, :tid, 'rls_isolation_test', now())
                    ON CONFLICT (id) DO NOTHING
                """),
                {
                    "id": str(audit_id_a),
                    "tid": str(_TENANT_A),
                },
            )

        # ── READ-A: tenant-A session must see the rows it owns ──
        async with get_db_session_for_tenant(str(_TENANT_A)) as sess_a:
            run_rows_a = (
                await sess_a.execute(
                    text("SELECT id FROM runs WHERE id = :id"),
                    {"id": str(run_id_a)},
                )
            ).fetchall()
            artifact_rows_a = (
                await sess_a.execute(
                    text("SELECT id FROM artifacts WHERE id = :id"),
                    {"id": str(artifact_id_a)},
                )
            ).fetchall()
            audit_rows_a = (
                await sess_a.execute(
                    text("SELECT id FROM audit_events WHERE id = :id"),
                    {"id": str(audit_id_a)},
                )
            ).fetchall()

        assert len(run_rows_a) == 1, (
            f"Tenant-A session must see the tenant-A run row, got {len(run_rows_a)} rows. "
            "Check that the GUC setter is working (app.current_tenant_id SET LOCAL)."
        )
        assert len(artifact_rows_a) == 1, (
            f"Tenant-A session must see the tenant-A artifact row, got {len(artifact_rows_a)} rows."
        )
        assert len(audit_rows_a) == 1, (
            f"Tenant-A session must see the tenant-A audit_event row, got {len(audit_rows_a)} rows."
        )

        # ── READ-B: tenant-B session must get EMPTY SET — NOT a 403, NOT an exception ──
        # This is the structural proof: RLS filters silently.
        async with get_db_session_for_tenant(str(_TENANT_B)) as sess_b:
            run_rows_b = (
                await sess_b.execute(
                    text("SELECT id FROM runs WHERE id = :id"),
                    {"id": str(run_id_a)},
                )
            ).fetchall()
            artifact_rows_b = (
                await sess_b.execute(
                    text("SELECT id FROM artifacts WHERE id = :id"),
                    {"id": str(artifact_id_a)},
                )
            ).fetchall()
            audit_rows_b = (
                await sess_b.execute(
                    text("SELECT id FROM audit_events WHERE id = :id"),
                    {"id": str(audit_id_a)},
                )
            ).fetchall()

        assert len(run_rows_b) == 0, (
            f"Tenant-B session MUST get an empty set for tenant-A run rows, got {len(run_rows_b)}. "
            "RLS is not enforcing isolation — check that migration 0006 (FORCE ROW LEVEL SECURITY) "
            "has been applied and that app.current_tenant_id GUC is being set correctly."
        )
        assert len(artifact_rows_b) == 0, (
            f"Tenant-B session MUST get an empty set for tenant-A artifact rows, "
            f"got {len(artifact_rows_b)}."
        )
        assert len(audit_rows_b) == 0, (
            f"Tenant-B session MUST get an empty set for tenant-A audit_event rows, "
            f"got {len(audit_rows_b)}."
        )

        # ── READ-A-AGAIN: confirm no GUC bleed from pool after tenant-B session ──
        # This validates the SET LOCAL + finally-reset pattern (Pitfall 1 defense).
        async with get_db_session_for_tenant(str(_TENANT_A)) as sess_a2:
            run_rows_a2 = (
                await sess_a2.execute(
                    text("SELECT id FROM runs WHERE id = :id"),
                    {"id": str(run_id_a)},
                )
            ).fetchall()

        assert len(run_rows_a2) == 1, (
            "Tenant-A session opened AFTER tenant-B session must still see tenant-A rows. "
            "Pool-connection GUC bleed detected (Pitfall 1) — check the finally-reset in "
            "get_db_session_for_tenant."
        )

    finally:
        # ── CLEANUP: remove test fixtures under a tenant-A session — the DELETE USING
        # policy (tenant_id = app.current_tenant_id) makes the tenant-A rows visible
        # and deletable; org/workspace have no RLS so they delete unconditionally. ──
        async with get_db_session_for_tenant(str(_TENANT_A)) as su_cleanup:
            await su_cleanup.execute(
                text("DELETE FROM audit_events WHERE id = :id"),
                {"id": str(audit_id_a)},
            )
            await su_cleanup.execute(
                text("DELETE FROM artifacts WHERE id = :id"),
                {"id": str(artifact_id_a)},
            )
            await su_cleanup.execute(
                text("DELETE FROM runs WHERE id = :id"),
                {"id": str(run_id_a)},
            )
            await su_cleanup.execute(
                text("DELETE FROM projects WHERE id = :id"),
                {"id": str(proj_a_id)},
            )
            await su_cleanup.execute(
                text("DELETE FROM workspaces WHERE id = :id"),
                {"id": str(ws_a_id)},
            )
            await su_cleanup.execute(
                text("DELETE FROM organizations WHERE id = :id"),
                {"id": str(org_a_id)},
            )
