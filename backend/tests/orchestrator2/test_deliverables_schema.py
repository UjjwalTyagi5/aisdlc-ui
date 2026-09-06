"""The deliverables table, and the one thing about it that can silently drift.

The `agent_id` CHECK constraint duplicates the registry. If a tenth agent is added
to `AGENT_IDS` and this constraint is not migrated with it, that agent's output is
rejected by the database at write time — which, because capture is deliberately
non-fatal, surfaces as an agent that produced nothing. Exactly the §2.4 failure
this rebuild exists to eliminate. This test is the drift guard.
"""
import re
from pathlib import Path

MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "migrations" / "versions" / "0044_orchestrator_deliverables.py"
)


def test_agent_id_check_constraint_matches_the_registry():
    from agents_orchestrator.orchestrator2.registry import AGENT_IDS

    source = MIGRATION.read_text(encoding="utf-8")
    match = re.search(r"agent_id IN \(([^)]*)\)", source, re.S)
    assert match, "the agent_id CHECK constraint is missing from the migration"
    in_constraint = set(re.findall(r"'([a-z_]+)'", match.group(1)))
    assert in_constraint == set(AGENT_IDS), (
        f"constraint {sorted(in_constraint)} != registry {sorted(AGENT_IDS)}"
    )


def test_table_has_no_approval_column():
    """A Deliverable is not an Artifact. The absence of approval is the point:
    the Orchestrator is Project-Admin-only and has no gates (spec D5), so a
    column named `approval_status` here would be a contradiction someone would
    later 'fix' by wiring a gate into a surface that must not have one.

    Asserts on the COLUMN, not on the word. The docstring explains at length why
    there is no approval here, and a substring check over the whole file would be
    failed by that explanation — then 'fixed' by deleting the reasoning, which is
    the most useful text in the file.
    """
    from shared.models.orm import OrchestratorDeliverable

    source = MIGRATION.read_text(encoding="utf-8")
    columns = set(re.findall(r'sa\.Column\(\s*"([a-z_]+)"', source))
    assert "approval_status" not in columns
    assert "approval_status" not in OrchestratorDeliverable.__table__.columns.keys()


def test_rls_keys_off_current_tenant_id():
    """`app.tenant_id` matches nothing and reads as a permanently empty table,
    which looks exactly like 'no deliverables yet'. See 0043's docstring."""
    source = MIGRATION.read_text(encoding="utf-8")
    assert "app.current_tenant_id" in source
    assert "current_setting('app.tenant_id'" not in source
    assert "FORCE ROW LEVEL SECURITY" in source


def test_orm_model_matches_the_migration_columns():
    """A column in one and not the other is a write that fails at runtime only.

    The ORM is what `capture()` instantiates; the migration is what exists in the
    database. They are written by hand in two files, so nothing but this test stops
    them drifting apart.
    """
    from shared.models.orm import OrchestratorDeliverable

    source = MIGRATION.read_text(encoding="utf-8")
    in_migration = set(re.findall(r'sa\.Column\(\s*"([a-z_]+)"', source))
    in_orm = set(OrchestratorDeliverable.__table__.columns.keys())
    assert in_orm == in_migration, (
        f"orm-only {sorted(in_orm - in_migration)}, "
        f"migration-only {sorted(in_migration - in_orm)}"
    )
