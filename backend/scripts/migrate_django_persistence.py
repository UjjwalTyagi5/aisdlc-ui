#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""One-shot idempotent migration: Django legacy tables → new Postgres tables.

Copies agent_session → agent_sessions and orchestrator_state_sdlc → orchestrator_state.
Safe to re-run (UPSERT by PK). If the legacy DB is unreachable or tables are absent,
exits 0 with a clear message — that is the expected normal case once Django is gone.

Usage:
    python scripts/migrate_django_persistence.py --django-dsn postgresql://user:pass@host/legacy_db
    python scripts/migrate_django_persistence.py --django-dsn <dsn> --dry-run
    python scripts/migrate_django_persistence.py --dry-run           # no-op path, exits 0
"""
import argparse
import json
import logging
import pathlib
import sys

# Make platform/backend importable when run as `python scripts/migrate_django_persistence.py`
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

logging.basicConfig(
    format="%(asctime)s %(levelname)-8s %(message)s",
    level=logging.INFO,
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger("migrate_django_persistence")

# ---------------------------------------------------------------------------
# Source table names (legacy Django, using explicit db_table)
# ---------------------------------------------------------------------------
SRC_SESSIONS_TABLE = "agent_session"
SRC_ORCH_TABLE = "orchestrator_state_sdlc"

# ---------------------------------------------------------------------------
# DB driver — psycopg2 is listed in pyproject.toml (psycopg2-binary==2.9.12).
# psycopg (v3) is also available but psycopg2 + SQLAlchemy is the pattern
# already established by m2_migrate_sessions.py, so we keep consistency.
# ---------------------------------------------------------------------------

def _make_engine(dsn: str, label: str):
    """Return a synchronous SQLAlchemy engine, converting asyncpg DSN if needed."""
    try:
        import psycopg2  # noqa: F401
    except ImportError:
        sys.exit(
            "ERROR: psycopg2 is not installed. Run: pip install psycopg2-binary"
        )
    from sqlalchemy import create_engine

    # Normalise: strip asyncpg dialect prefix if present
    sync_dsn = dsn.replace("postgresql+asyncpg://", "postgresql+psycopg2://")
    if not (
        sync_dsn.startswith("postgresql://")
        or sync_dsn.startswith("postgresql+psycopg2://")
    ):
        sync_dsn = "postgresql+psycopg2://" + sync_dsn.split("://", 1)[-1]

    log.debug("Creating engine for %s: %s", label, sync_dsn.split("@")[-1])
    return create_engine(sync_dsn, echo=False)


def _resolve_dest_dsn() -> str:
    """Return a sync DSN for the destination (app) DB.

    Prefers POSTGRES_SYNC_CONN_STRING (already a native postgresql:// URL used by
    psycopg/PostgresSaver). Falls back to converting POSTGRES_CONN_STRING from asyncpg
    format. Both come from config.env; falls back to os.environ if config.env fails to
    import (e.g. missing mandatory vars irrelevant to this script).
    """
    try:
        from config.env import POSTGRES_SYNC_CONN_STRING, POSTGRES_CONN_STRING
    except Exception:
        import os
        POSTGRES_SYNC_CONN_STRING = os.environ.get("POSTGRES_SYNC_CONN_STRING", "")
        POSTGRES_CONN_STRING = os.environ.get("POSTGRES_CONN_STRING", "")

    if POSTGRES_SYNC_CONN_STRING:
        return POSTGRES_SYNC_CONN_STRING
    if POSTGRES_CONN_STRING:
        return POSTGRES_CONN_STRING.replace("postgresql+asyncpg://", "postgresql://")
    return ""


# ---------------------------------------------------------------------------
# JSON coercion helper
# ---------------------------------------------------------------------------

def _coerce_json(val):
    """Return a JSON-serialisable dict/list from val, or None if val is None.

    Handles:
    - Already a dict/list (pass-through)
    - Stringified JSON (json.loads)
    - Any other string (wrap as {"raw": val})
    - Non-string scalars (wrap as {"raw": str(val)})
    """
    if val is None:
        return None
    if isinstance(val, (dict, list)):
        return val
    if isinstance(val, str):
        val_stripped = val.strip()
        if not val_stripped:
            return None
        try:
            return json.loads(val_stripped)
        except (json.JSONDecodeError, ValueError):
            return {"raw": val}
    return {"raw": str(val)}


def _json_str(val) -> str | None:
    """Coerce val and return as a JSON string for psycopg2 JSONB binding."""
    coerced = _coerce_json(val)
    if coerced is None:
        return None
    return json.dumps(coerced)


# ---------------------------------------------------------------------------
# Read from legacy source DB
# ---------------------------------------------------------------------------

def _read_source_table(conn, table: str) -> list[dict]:
    """Read all rows from table. Returns [] on any error (table absent, etc.)."""
    from sqlalchemy import text
    result = conn.execute(text(f"SELECT * FROM {table}"))  # noqa: S608
    columns = list(result.keys())
    rows = [dict(zip(columns, row)) for row in result.fetchall()]
    log.info("Read %d rows from legacy table %s", len(rows), table)
    return rows


# ---------------------------------------------------------------------------
# Map + upsert: agent_session → agent_sessions
# ---------------------------------------------------------------------------

def _upsert_agent_sessions(rows: list[dict], dest_conn, dry_run: bool) -> int:
    """UPSERT rows into agent_sessions. Returns count of rows processed."""
    if not rows:
        log.info("No rows to migrate for agent_sessions.")
        return 0

    if dry_run:
        log.info("DRY-RUN: would upsert %d row(s) into agent_sessions.", len(rows))
        return len(rows)

    from sqlalchemy import text

    upserted = 0
    skipped = 0
    for row in rows:
        params = {
            "session_id":             row.get("session_id"),
            "agent_type":             row.get("agent_type"),
            "user_id":                row.get("user_id"),
            # tenant_id is new in agent_sessions; legacy table has none → NULL
            "tenant_id":              None,
            "requirements_payload":   _json_str(row.get("requirements_payload")),
            "design_artifacts":       _json_str(row.get("design_artifacts")),
            "development_artifacts":  _json_str(row.get("development_artifacts")),
            "testing_artifacts":      _json_str(row.get("testing_artifacts")),
            # new columns absent from legacy source → NULL
            "code_review_artifacts":  None,
            "security_artifacts":     None,
            "deployment_artifacts":   None,
            "last_handoff_event":     _json_str(row.get("last_handoff_event")),
            "current_stage":          row.get("current_stage"),
            "artifact_version":       row.get("artifact_version", 1) or 1,
            "created_at":             row.get("created_at"),
            "updated_at":             row.get("updated_at"),
        }
        try:
            dest_conn.execute(text("""
                INSERT INTO agent_sessions (
                    session_id, agent_type, user_id, tenant_id,
                    requirements_payload, design_artifacts,
                    development_artifacts, testing_artifacts,
                    code_review_artifacts, security_artifacts, deployment_artifacts,
                    last_handoff_event, current_stage, artifact_version,
                    created_at, updated_at
                ) VALUES (
                    :session_id, :agent_type, :user_id, :tenant_id::uuid,
                    :requirements_payload::jsonb, :design_artifacts::jsonb,
                    :development_artifacts::jsonb, :testing_artifacts::jsonb,
                    :code_review_artifacts::jsonb, :security_artifacts::jsonb,
                    :deployment_artifacts::jsonb,
                    :last_handoff_event::jsonb, :current_stage, :artifact_version,
                    :created_at, :updated_at
                )
                ON CONFLICT (session_id) DO UPDATE SET
                    agent_type            = EXCLUDED.agent_type,
                    user_id               = EXCLUDED.user_id,
                    requirements_payload  = EXCLUDED.requirements_payload,
                    design_artifacts      = EXCLUDED.design_artifacts,
                    development_artifacts = EXCLUDED.development_artifacts,
                    testing_artifacts     = EXCLUDED.testing_artifacts,
                    last_handoff_event    = EXCLUDED.last_handoff_event,
                    current_stage         = EXCLUDED.current_stage,
                    artifact_version      = EXCLUDED.artifact_version,
                    updated_at            = EXCLUDED.updated_at
            """), params)
            upserted += 1
        except Exception as exc:
            log.warning("Failed to upsert session_id=%s: %s", row.get("session_id"), exc)
            skipped += 1

    log.info("agent_sessions: %d upserted, %d skipped", upserted, skipped)
    return upserted


# ---------------------------------------------------------------------------
# Map + upsert: orchestrator_state_sdlc → orchestrator_state
# ---------------------------------------------------------------------------

def _upsert_orchestrator_state(rows: list[dict], dest_conn, dry_run: bool) -> int:
    """UPSERT rows into orchestrator_state. Returns count of rows processed."""
    if not rows:
        log.info("No rows to migrate for orchestrator_state.")
        return 0

    if dry_run:
        log.info("DRY-RUN: would upsert %d row(s) into orchestrator_state.", len(rows))
        return len(rows)

    from sqlalchemy import text

    upserted = 0
    skipped = 0
    for row in rows:
        params = {
            "chat_session_id":      row.get("chat_session_id"),
            "current_active_agent": row.get("current_active_agent"),
            "current_batch_id":     row.get("current_batch_id"),
            "pending_user_gate":    row.get("pending_user_gate"),
            "last_handoff_event":   _json_str(row.get("last_handoff_event")),
            "updated_at":           row.get("updated_at"),
        }
        try:
            dest_conn.execute(text("""
                INSERT INTO orchestrator_state (
                    chat_session_id, current_active_agent, current_batch_id,
                    pending_user_gate, last_handoff_event, updated_at
                ) VALUES (
                    :chat_session_id, :current_active_agent, :current_batch_id,
                    :pending_user_gate, :last_handoff_event::jsonb, :updated_at
                )
                ON CONFLICT (chat_session_id) DO UPDATE SET
                    current_active_agent = EXCLUDED.current_active_agent,
                    current_batch_id     = EXCLUDED.current_batch_id,
                    pending_user_gate    = EXCLUDED.pending_user_gate,
                    last_handoff_event   = EXCLUDED.last_handoff_event,
                    updated_at           = EXCLUDED.updated_at
            """), params)
            upserted += 1
        except Exception as exc:
            log.warning("Failed to upsert chat_session_id=%s: %s",
                        row.get("chat_session_id"), exc)
            skipped += 1

    log.info("orchestrator_state: %d upserted, %d skipped", upserted, skipped)
    return upserted


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "One-shot idempotent migration: Django legacy tables → new Postgres tables.\n"
            "No-op if --django-dsn is omitted or the legacy DB is unreachable."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--django-dsn",
        metavar="DSN",
        default="",
        help="Sync postgresql:// DSN for the legacy Django DB (source). "
             "If omitted, the no-op path runs and exits 0.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Read source rows and report intended counts; do not write.",
    )
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    django_dsn = (args.django_dsn or "").strip()

    if not django_dsn:
        print(
            "No --django-dsn provided — "
            "no legacy Django DB reachable / tables absent — nothing to migrate."
        )
        sys.exit(0)

    # --- Attempt to connect to legacy source DB ---
    try:
        src_engine = _make_engine(django_dsn, "legacy-django")
        from sqlalchemy import text

        with src_engine.connect() as src_conn:
            # Verify tables exist before reading
            try:
                src_conn.execute(text(f"SELECT 1 FROM {SRC_SESSIONS_TABLE} LIMIT 1"))  # noqa: S608
                src_conn.execute(text(f"SELECT 1 FROM {SRC_ORCH_TABLE} LIMIT 1"))  # noqa: S608
            except Exception as tbl_err:
                print(
                    f"no legacy Django DB reachable / tables absent — nothing to migrate. "
                    f"({tbl_err})"
                )
                sys.exit(0)

            session_rows = _read_source_table(src_conn, SRC_SESSIONS_TABLE)
            orch_rows = _read_source_table(src_conn, SRC_ORCH_TABLE)

    except Exception as conn_err:
        print(
            f"no legacy Django DB reachable / tables absent — nothing to migrate. "
            f"({conn_err})"
        )
        sys.exit(0)

    print(f"\n--- Source row counts ---")
    print(f"  {SRC_SESSIONS_TABLE}       : {len(session_rows)}")
    print(f"  {SRC_ORCH_TABLE}  : {len(orch_rows)}")

    if args.dry_run:
        print(f"\n--- Dry-run: intended upserts ---")
        print(f"  agent_sessions    : {len(session_rows)}")
        print(f"  orchestrator_state: {len(orch_rows)}")
        print("\nDRY-RUN complete — no data written.")
        sys.exit(0)

    # --- Connect to destination DB ---
    dest_dsn = _resolve_dest_dsn()
    if not dest_dsn:
        log.error(
            "Destination DSN not found. "
            "Set POSTGRES_SYNC_CONN_STRING or POSTGRES_CONN_STRING in your environment."
        )
        sys.exit(1)

    dest_engine = _make_engine(dest_dsn, "destination-postgres")

    with dest_engine.connect() as dest_conn:
        with dest_conn.begin():
            s_count = _upsert_agent_sessions(session_rows, dest_conn, dry_run=False)
            o_count = _upsert_orchestrator_state(orch_rows, dest_conn, dry_run=False)

    print(f"\n--- Migration complete ---")
    print(f"  agent_sessions upserted    : {s_count}")
    print(f"  orchestrator_state upserted: {o_count}")


if __name__ == "__main__":
    main()
