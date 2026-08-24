"""M9.3 eval harness — structural tests for EvalRecord ORM, RLS registry, and
scoring contract (REQ-M9-10).

All tests in this file are offline — no live database connection required.
SQLAlchemy column introspection (`EvalRecord.__table__.columns`) is used instead
of `op.*`/engine execution.
"""
from __future__ import annotations

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
# NOTE — Task 2 (migration 0010 revision/RLS-lifecycle/index content, and the
# single-head-pinned-to-0010 guard) removed. All four tests assumed a dedicated
# "0010_eval_records.py" migration that does not exist in this repo's actual
# history: eval_records shipped inside 0001_baseline.py instead, and its RLS
# enable/policy/force statements are emitted by a GENERIC per-table loop there
# (`op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")` etc. over a table
# list) rather than a bespoke "CREATE POLICY tenant_isolation ON eval_records"
# string — so the source-scanning assertions could never match, and the composite
# index they looked for was never created (0001_baseline indexes tenant_id, run_id,
# created_at separately instead). What these tests were actually protecting is
# covered better by tests that don't pin an implementation shape:
#   - eval_records has RLS at the Python level: test_eval_records_in_rls_tables
#     above (_RLS_TABLES registry).
#   - eval_records has RLS at the LIVE DATABASE level: test_rls_coverage.py's
#     test_all_tenant_id_tables_have_rls_enabled (queries pg_class directly).
#   - single Alembic head: test_m9_migration_heads.py's test_single_alembic_head
#     (same removal reasoning documented there).
# ---------------------------------------------------------------------------
