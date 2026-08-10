"""M9.3 eval harness — structural tests for EvalRecord ORM, RLS registry, scoring
contract, and migration 0010 (REQ-M9-10).

All tests in this file are offline — no live database connection required.
SQLAlchemy column introspection (`EvalRecord.__table__.columns`) and source-string
scanning of the migration file are used instead of `op.*`/engine execution.
"""
from __future__ import annotations

import os

from alembic.config import Config
from alembic.script import ScriptDirectory

from shared.eval.scoring import EvalSignals, score_output
from shared.models.orm import _RLS_TABLES, EvalRecord


# ---------------------------------------------------------------------------
# Task 1: EvalRecord ORM columns + _RLS_TABLES registration + scoring contract
# ---------------------------------------------------------------------------


def test_eval_record_columns():
    columns = EvalRecord.__table__.columns

    assert EvalRecord.__tablename__ == "eval_records"

    tenant_id = columns["tenant_id"]
    assert tenant_id.nullable is False
    assert tenant_id.index is True

    run_id = columns["run_id"]
    assert run_id.index is True
    assert run_id.type.length == 255

    agent_type = columns["agent_type"]
    assert agent_type.nullable is False
    assert agent_type.type.length == 50

    score = columns["score"]
    assert score.nullable is True
    assert score.type.precision == 5
    assert score.type.scale == 4

    signals = columns["signals"]
    assert signals.nullable is True

    created_at = columns["created_at"]
    assert created_at.index is True
    assert created_at.type.timezone is True
    assert created_at.server_default is not None

    # Append-only — no updated_at column
    assert "updated_at" not in columns


def test_eval_records_in_rls_tables():
    assert "eval_records" in _RLS_TABLES
    # The original 5 tenant-scoped FORCE-RLS tables (0001/0006) must still be present.
    for table in ("projects", "runs", "artifacts", "audit_events", "agent_call_logs"):
        assert table in _RLS_TABLES


def test_score_output_contract_perfect_match():
    result = score_output("requirements", "alpha beta gamma", "alpha beta gamma")
    assert isinstance(result, EvalSignals)
    assert result.score == 1.0
    assert isinstance(result.signals, dict)


def test_score_output_contract_total_mismatch():
    result = score_output("requirements", "alpha beta gamma", "delta epsilon zeta")
    assert isinstance(result, EvalSignals)
    assert result.score == 0.0


def test_score_output_contract_design_sections():
    actual = (
        "## High-Level Design (HLD)\n...\n"
        "## Low-Level Design (LLD)\n...\n"
        "## API Contracts\n...\n"
    )
    result = score_output("design_architecture", actual, expected="")
    assert isinstance(result, EvalSignals)
    assert 0.0 < result.score < 1.0
    assert result.signals["present_count"] == 3


def test_score_output_in_bounds_range():
    for actual, expected in [
        (None, "expected text"),
        ("", ""),
        ("some completely unrelated text here", "alpha beta gamma delta"),
    ]:
        result = score_output("requirements", actual, expected)
        assert 0.0 <= result.score <= 1.0


# ---------------------------------------------------------------------------
# Task 2: Migration 0010 — table + full RLS lifecycle (ENABLE+POLICY+FORCE)
# ---------------------------------------------------------------------------


def _agentic_app_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _migration_0010_source() -> str:
    path = os.path.join(
        _agentic_app_root(), "migrations", "versions", "0010_eval_records.py"
    )
    with open(path, encoding="utf-8") as f:
        return f.read()


def _script_directory() -> ScriptDirectory:
    agentic_app_root = _agentic_app_root()
    alembic_ini_path = os.path.join(agentic_app_root, "alembic.ini")
    config = Config(alembic_ini_path)
    config.set_main_option(
        "script_location", os.path.join(agentic_app_root, "migrations")
    )
    return ScriptDirectory.from_config(config)


def test_migration_0010_revision_chain():
    script_dir = _script_directory()
    revision = script_dir.get_revision("0010")
    assert revision is not None
    assert revision.revision == "0010"
    assert revision.down_revision == "0009"


def test_migration_0010_full_rls_lifecycle():
    source = _migration_0010_source()

    assert "ENABLE ROW LEVEL SECURITY" in source
    assert "CREATE POLICY tenant_isolation ON eval_records" in source
    assert "CREATE POLICY tenant_isolation_insert ON eval_records" in source
    assert "FORCE ROW LEVEL SECURITY" in source
    assert "current_setting('app.current_tenant_id'" in source


def test_migration_0010_composite_index_in_autocommit_block():
    source = _migration_0010_source()

    assert "ix_eval_tenant_run_created" in source
    assert "autocommit_block" in source
    assert "CONCURRENTLY" in source


def test_single_alembic_head_after_0010():
    """REQ-M9-00 guard: 0010 must chain off 0009 as the single Alembic head."""
    script_dir = _script_directory()
    heads = script_dir.get_heads()
    assert len(heads) == 1, f"expected exactly 1 head, got {heads}"
    assert heads[0] == "0010"
