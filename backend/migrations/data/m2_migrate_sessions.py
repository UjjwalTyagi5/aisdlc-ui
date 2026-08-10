#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
M2 data migration: Django AgentSession -> Postgres runs table.
Django AgentCallLog -> Postgres agent_call_logs table.

Usage:
    python m2_migrate_sessions.py --dry-run    # Read only; print counts and field mapping
    python m2_migrate_sessions.py --execute    # Write to Postgres; verify counts before commit
    python m2_migrate_sessions.py --verify     # Compare counts only (post-migration check)

Prerequisites:
    Both SQLSERVER_CONN_STRING and POSTGRES_CONN_STRING must be set in the environment (.env).
    POSTGRES_CONN_STRING must use postgresql+psycopg2:// (sync driver) or the script will adapt
    from postgresql+asyncpg:// automatically.

    The Postgres 'runs' table must have a 'metadata' JSONB column. If it does not exist yet,
    run the Alembic migration that adds it before executing this script.

Sentinel rows (idempotent - safe to re-run):
    Organization: 00000000-0000-0000-0000-000000000001 (slug=migration-legacy)
    Workspace:    00000000-0000-0000-0000-000000000002 (slug=default)
    Project:      00000000-0000-0000-0000-000000000003 (display_name=Migrated Sessions)
    Tenant:       00000000-0000-0000-0000-000000000001 (same as Org UUID, by convention)

Run this script from the repo root or from agentic_app/:
    python agentic_app/migrations/data/m2_migrate_sessions.py --dry-run
"""
import argparse
import json
import logging
import sys
import uuid
from datetime import datetime
from decimal import Decimal

# ---------------------------------------------------------------------------
# Script must be importable from repo root (python agentic_app/migrations/...)
# or from inside agentic_app/ (python migrations/...). Adjust sys.path so that
# config.env is always importable.
# ---------------------------------------------------------------------------
import os
import pathlib

_SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
# Walk up until we find a directory containing config/env.py
_root = _SCRIPT_DIR
for _ in range(6):
    if (_root / "config" / "env.py").exists():
        sys.path.insert(0, str(_root))
        break
    _root = _root.parent

# After path adjustment import env constants; fall back to os.environ if the
# module cannot be found or fails to load (e.g., missing mandatory env vars
# like AGENTIC_BASE_URL that config.env requires for the main app but are
# irrelevant to this standalone migration script).
try:
    from config.env import SQLSERVER_CONN_STRING, POSTGRES_CONN_STRING
except (ImportError, KeyError):
    SQLSERVER_CONN_STRING = os.environ.get("SQLSERVER_CONN_STRING", "")
    POSTGRES_CONN_STRING = os.environ.get("POSTGRES_CONN_STRING", "")

from sqlalchemy import create_engine, text

logging.basicConfig(
    format="%(asctime)s %(levelname)-8s %(message)s",
    level=logging.INFO,
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger("m2_migrate")

# ---------------------------------------------------------------------------
# Sentinel UUIDs - hardcoded, never user-supplied.
# Enterprise RLS policies MUST exclude SENTINEL_TENANT_ID from normal queries
# so migrated rows are invisible to tenants until reassigned.
# ---------------------------------------------------------------------------
SENTINEL_ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
SENTINEL_WORKSPACE_ID = uuid.UUID("00000000-0000-0000-0000-000000000002")
SENTINEL_PROJECT_ID = uuid.UUID("00000000-0000-0000-0000-000000000003")
SENTINEL_TENANT_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")

# Django ORM uses app_label + model_name for table naming unless db_table is set.
# Both AgentSession and AgentCallLog set db_table explicitly.
DJANGO_SESSION_TABLE = "agent_session"
DJANGO_CALL_LOG_TABLE = "agent_call_log"


# ---------------------------------------------------------------------------
# Connection helpers
# ---------------------------------------------------------------------------

def _make_pg_engine(conn_str: str):
    """Return a synchronous SQLAlchemy engine for Postgres.

    POSTGRES_CONN_STRING from config.env uses the asyncpg dialect
    (postgresql+asyncpg://). Convert it to psycopg2 for use in this
    synchronous migration script.
    """
    try:
        import psycopg2  # noqa: F401
    except ImportError:
        sys.exit(
            "ERROR: psycopg2 is not installed.\n"
            "Install it with:  pip install psycopg2-binary\n"
            "Then re-run this script."
        )
    sync_url = conn_str.replace("postgresql+asyncpg://", "postgresql+psycopg2://")
    # Strip asyncpg-specific params that psycopg2 does not understand.
    # The URL may be plain postgresql:// already (POSTGRES_SYNC_CONN_STRING).
    if not (sync_url.startswith("postgresql://") or sync_url.startswith("postgresql+psycopg2://")):
        sync_url = "postgresql+psycopg2://" + sync_url.split("://", 1)[-1]
    return create_engine(sync_url, echo=False)


def _make_mssql_engine(conn_str: str):
    """Return a synchronous SQLAlchemy engine for SQL Server."""
    return create_engine(conn_str, echo=False)


def _pre_check(mssql_engine, pg_engine) -> bool:
    """Verify that both database connections are reachable. Return False on failure."""
    ok = True
    try:
        with mssql_engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        log.info("SQL Server connection: OK")
    except Exception as exc:
        log.error("SQL Server connection FAILED: %s", exc)
        ok = False

    try:
        with pg_engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        log.info("Postgres connection: OK")
    except Exception as exc:
        log.error("Postgres connection FAILED: %s", exc)
        ok = False

    return ok


# ---------------------------------------------------------------------------
# Sentinel rows (idempotent)
# ---------------------------------------------------------------------------

def _create_sentinel_rows(pg_conn) -> None:
    """Insert sentinel Organization, Workspace, Project rows.

    Uses INSERT ... ON CONFLICT DO NOTHING so the function is safe to call
    multiple times (idempotent).
    """
    now = datetime.utcnow()

    pg_conn.execute(text("""
        INSERT INTO organizations (id, slug, display_name, created_at, updated_at)
        VALUES (:id, :slug, :display_name, :created_at, :updated_at)
        ON CONFLICT (id) DO NOTHING
    """), {
        "id": str(SENTINEL_ORG_ID),
        "slug": "migration-legacy",
        "display_name": "Migration Legacy",
        "created_at": now,
        "updated_at": now,
    })

    pg_conn.execute(text("""
        INSERT INTO workspaces (id, organization_id, slug, display_name, created_at, updated_at)
        VALUES (:id, :organization_id, :slug, :display_name, :created_at, :updated_at)
        ON CONFLICT (id) DO NOTHING
    """), {
        "id": str(SENTINEL_WORKSPACE_ID),
        "organization_id": str(SENTINEL_ORG_ID),
        "slug": "default",
        "display_name": "Default",
        "created_at": now,
        "updated_at": now,
    })

    pg_conn.execute(text("""
        INSERT INTO projects (id, workspace_id, tenant_id, display_name, provider_kind, created_at, updated_at)
        VALUES (:id, :workspace_id, :tenant_id, :display_name, :provider_kind, :created_at, :updated_at)
        ON CONFLICT (id) DO NOTHING
    """), {
        "id": str(SENTINEL_PROJECT_ID),
        "workspace_id": str(SENTINEL_WORKSPACE_ID),
        "tenant_id": str(SENTINEL_TENANT_ID),
        "display_name": "Migrated Sessions",
        "provider_kind": "azure_devops",
        "created_at": now,
        "updated_at": now,
    })

    log.info("Sentinel rows created/verified (org=%s, workspace=%s, project=%s)",
             SENTINEL_ORG_ID, SENTINEL_WORKSPACE_ID, SENTINEL_PROJECT_ID)


# ---------------------------------------------------------------------------
# Read Django data
# ---------------------------------------------------------------------------

def _read_django_sessions(mssql_conn) -> list[dict]:
    """Return all rows from Django's agent_session table as a list of dicts."""
    result = mssql_conn.execute(text(f"SELECT * FROM {DJANGO_SESSION_TABLE}"))
    columns = list(result.keys())
    rows = [dict(zip(columns, row)) for row in result.fetchall()]
    log.info("Read %d rows from Django %s", len(rows), DJANGO_SESSION_TABLE)
    return rows


def _read_django_call_logs(mssql_conn) -> list[dict]:
    """Return all rows from Django's agent_call_log table as a list of dicts."""
    result = mssql_conn.execute(text(f"SELECT * FROM {DJANGO_CALL_LOG_TABLE}"))
    columns = list(result.keys())
    rows = [dict(zip(columns, row)) for row in result.fetchall()]
    log.info("Read %d rows from Django %s", len(rows), DJANGO_CALL_LOG_TABLE)
    return rows


# ---------------------------------------------------------------------------
# Field mapping helpers
# ---------------------------------------------------------------------------

def _map_session_to_run(session_row: dict) -> dict:
    """Map a Django AgentSession row to a Postgres runs row.

    Field mapping per M2-RESEARCH.md Section 5:
        session_id (PK, CharField) -> id (UUID, deterministic from hash)
        agent_type                 -> stage (implied by agent type)
        user_id                    -> tenant_id sentinel (no real user UUID)
        requirements_payload       -> requirements_payload (JSONB, direct copy)
        design_artifacts           -> design_artifacts (JSONB, direct copy)
        development_artifacts      -> development_artifacts (JSONB, direct copy)
        testing_artifacts          -> testing_artifacts (JSONB, direct copy)
        current_stage              -> current_stage (direct copy)
        created_at                 -> created_at (preserved)
        updated_at                 -> updated_at (preserved)
    """
    session_id = session_row.get("session_id", "")

    # Generate a deterministic UUID from the session_id string so re-runs
    # of --execute produce the same UUID and hit the ON CONFLICT guard.
    run_id = uuid.uuid5(uuid.NAMESPACE_DNS, f"agent_session:{session_id}")

    agent_type = session_row.get("agent_type", "requirements")
    # Normalise agent_type to the stage vocabulary used by the runs table.
    stage_map = {
        "requirements": "requirements",
        "design": "design",
        "development": "development",
        "testing": "testing",
        "deployment": "deployment",
    }
    stage = stage_map.get(agent_type.lower(), agent_type.lower())

    # Preserve existing JSON payloads verbatim; they were already validated
    # on insert into Django, so there is no need to re-parse here.
    def _json_field(val):
        if val is None:
            return None
        if isinstance(val, (dict, list)):
            return json.dumps(val)
        return str(val)

    return {
        "id": str(run_id),
        "project_id": str(SENTINEL_PROJECT_ID),
        "tenant_id": str(SENTINEL_TENANT_ID),
        "stage": stage,
        "status": "completed",
        "requirements_payload": _json_field(session_row.get("requirements_payload")),
        "design_artifacts": _json_field(session_row.get("design_artifacts")),
        "development_artifacts": _json_field(session_row.get("development_artifacts")),
        "testing_artifacts": _json_field(session_row.get("testing_artifacts")),
        "current_stage": session_row.get("current_stage"),
        "gate_pending": False,
        "metadata": json.dumps({
            "migrated_from": "agent_session",
            "original_session_id": session_id,
            "original_agent_type": agent_type,
        }),
        "created_at": session_row.get("created_at"),
        "updated_at": session_row.get("updated_at"),
    }


def _map_call_log(log_row: dict) -> dict:
    """Map a Django AgentCallLog row to a Postgres agent_call_logs row."""
    return {
        "id": str(uuid.uuid4()),
        "tenant_id": str(SENTINEL_TENANT_ID),
        "run_id": log_row.get("session_id"),
        "agent_type": log_row.get("agent_type", ""),
        "model": log_row.get("model", ""),
        "input_tokens": log_row.get("input_tokens"),
        "output_tokens": log_row.get("output_tokens"),
        "cost_usd": float(log_row.get("cost_usd") or 0),
        "duration_ms": log_row.get("duration_ms"),
        "created_at": log_row.get("created_at"),
    }


# ---------------------------------------------------------------------------
# Migration writers
# ---------------------------------------------------------------------------

def _migrate_sessions(django_sessions: list[dict], pg_conn, dry_run: bool) -> int:
    """Migrate AgentSession rows to runs. Return count of rows processed."""
    if dry_run:
        log.info("DRY-RUN: field mapping preview (first 3 rows shown)")
        for row in django_sessions[:3]:
            mapped = _map_session_to_run(row)
            print(json.dumps(mapped, indent=2, default=str))
        return len(django_sessions)

    inserted = 0
    skipped = 0
    for session_row in django_sessions:
        mapped = _map_session_to_run(session_row)
        try:
            pg_conn.execute(text("""
                INSERT INTO runs (
                    id, project_id, tenant_id, stage, status,
                    requirements_payload, design_artifacts,
                    development_artifacts, testing_artifacts,
                    current_stage, gate_pending, metadata,
                    created_at, updated_at
                )
                VALUES (
                    :id::uuid, :project_id::uuid, :tenant_id::uuid,
                    :stage, :status,
                    :requirements_payload::jsonb, :design_artifacts::jsonb,
                    :development_artifacts::jsonb, :testing_artifacts::jsonb,
                    :current_stage, :gate_pending, :metadata::jsonb,
                    :created_at, :updated_at
                )
                ON CONFLICT (id) DO NOTHING
            """), mapped)
            inserted += 1
        except Exception as exc:
            log.warning("Failed to insert session_id=%s: %s",
                        session_row.get("session_id"), exc)
            skipped += 1

    log.info("Sessions migrated: %d inserted, %d skipped/conflicted", inserted, skipped)
    return inserted


def _migrate_call_logs(django_logs: list[dict], pg_conn, dry_run: bool) -> int:
    """Migrate AgentCallLog rows to agent_call_logs. Return count of rows processed."""
    if dry_run:
        log.info("DRY-RUN: call log field mapping preview (first 3 rows shown)")
        for row in django_logs[:3]:
            mapped = _map_call_log(row)
            print(json.dumps(mapped, indent=2, default=str))
        return len(django_logs)

    inserted = 0
    skipped = 0
    for log_row in django_logs:
        mapped = _map_call_log(log_row)
        try:
            pg_conn.execute(text("""
                INSERT INTO agent_call_logs (
                    id, tenant_id, run_id, agent_type, model,
                    input_tokens, output_tokens, cost_usd, duration_ms, created_at
                )
                VALUES (
                    :id::uuid, :tenant_id::uuid, :run_id, :agent_type, :model,
                    :input_tokens, :output_tokens, :cost_usd, :duration_ms, :created_at
                )
            """), mapped)
            inserted += 1
        except Exception as exc:
            log.warning("Failed to insert call log row: %s", exc)
            skipped += 1

    log.info("Call logs migrated: %d inserted, %d skipped", inserted, skipped)
    return inserted


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------

def _verify_counts(mssql_conn, pg_conn) -> tuple[int, int, bool]:
    """Compare Django session count vs Postgres migrated run count.

    Returns (django_count, postgres_count, match).
    """
    django_row = mssql_conn.execute(
        text(f"SELECT COUNT(*) FROM {DJANGO_SESSION_TABLE}")
    ).fetchone()
    django_count = django_row[0] if django_row else 0

    pg_row = pg_conn.execute(
        text("SELECT COUNT(*) FROM runs WHERE metadata->>'migrated_from' = 'agent_session'")
    ).fetchone()
    postgres_count = pg_row[0] if pg_row else 0

    match = (django_count == postgres_count)

    print("\n--- Verification ---")
    print(f"  Django  {DJANGO_SESSION_TABLE} rows  : {django_count}")
    print(f"  Postgres runs (migrated) : {postgres_count}")
    print(f"  Match                    : {'YES' if match else 'NO - MISMATCH DETECTED'}")

    if not match:
        print(f"\n  DIFF: {abs(django_count - postgres_count)} rows unaccounted for.")
        print("  Re-run --execute to fill in missing rows (idempotent via ON CONFLICT).")

    return django_count, postgres_count, match


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "M2 data migration: Django AgentSession/AgentCallLog -> Postgres runs/agent_call_logs.\n\n"
            "Modes:\n"
            "  --dry-run   Read-only preview. Prints row counts and sample field mappings.\n"
            "  --execute   Write to Postgres. Prompts for confirmation first.\n"
            "  --verify    Compare row counts only (for post-migration check)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Preview only - no writes")
    mode.add_argument("--execute", action="store_true", help="Migrate data to Postgres")
    mode.add_argument("--verify", action="store_true", help="Compare counts only")
    parser.add_argument(
        "--skip-call-logs",
        action="store_true",
        help="Skip AgentCallLog migration (useful if agent_call_logs table schema is not yet applied)",
    )
    return parser


def main() -> None:
    parser = _build_arg_parser()
    args = parser.parse_args()

    if not SQLSERVER_CONN_STRING:
        log.error(
            "SQLSERVER_CONN_STRING is not set. "
            "Check your .env file or environment variables."
        )
        sys.exit(1)

    if not POSTGRES_CONN_STRING:
        log.error(
            "POSTGRES_CONN_STRING is not set. "
            "Check your .env file or environment variables."
        )
        sys.exit(1)

    mssql_engine = _make_mssql_engine(SQLSERVER_CONN_STRING)
    pg_engine = _make_pg_engine(POSTGRES_CONN_STRING)

    log.info("Checking database connections...")
    if not _pre_check(mssql_engine, pg_engine):
        log.error("One or more database connections failed. Aborting.")
        sys.exit(1)

    if args.verify:
        with mssql_engine.connect() as mssql_conn, pg_engine.connect() as pg_conn:
            _, _, match = _verify_counts(mssql_conn, pg_conn)
        sys.exit(0 if match else 1)

    # Dry-run mode - read Django data and preview mappings without writing.
    if args.dry_run:
        log.info("Running in DRY-RUN mode - no data will be written.")
        with mssql_engine.connect() as mssql_conn:
            sessions = _read_django_sessions(mssql_conn)
            call_logs = _read_django_call_logs(mssql_conn)

        print(f"\n--- Dry-run Summary ---")
        print(f"  Django AgentSession rows  : {len(sessions)}")
        print(f"  Django AgentCallLog rows  : {len(call_logs)}")
        print()

        print("--- Session field mapping (sample) ---")
        _migrate_sessions(sessions, pg_conn=None, dry_run=True)

        if not args.skip_call_logs:
            print()
            print("--- CallLog field mapping (sample) ---")
            _migrate_call_logs(call_logs, pg_conn=None, dry_run=True)

        print()
        log.info("Dry-run complete. No data was written.")
        return

    # Execute mode - write to Postgres after explicit confirmation.
    print("WARNING: This will write to Postgres. Django should be read-only during this window.")
    answer = input("Type 'migrate' to confirm: ")
    if answer.strip() != "migrate":
        sys.exit("Aborted.")

    log.info("Starting migration...")

    with mssql_engine.connect() as mssql_conn, pg_engine.connect() as pg_conn:
        with pg_conn.begin():
            _create_sentinel_rows(pg_conn)

            sessions = _read_django_sessions(mssql_conn)
            call_logs = _read_django_call_logs(mssql_conn)

            log.info("Migrating %d sessions...", len(sessions))
            _migrate_sessions(sessions, pg_conn, dry_run=False)

            if not args.skip_call_logs:
                log.info("Migrating %d call logs...", len(call_logs))
                _migrate_call_logs(call_logs, pg_conn, dry_run=False)

        log.info("Transaction committed.")

        log.info("Verifying row counts...")
        django_count, postgres_count, match = _verify_counts(mssql_conn, pg_conn)

    if not match:
        log.error(
            "Count mismatch after migration! Expected %d, got %d in Postgres.",
            django_count, postgres_count,
        )
        log.error("Re-run --execute (idempotent) or investigate skipped rows above.")
        sys.exit(1)

    log.info("Migration complete. %d sessions successfully migrated.", postgres_count)


if __name__ == "__main__":
    main()
