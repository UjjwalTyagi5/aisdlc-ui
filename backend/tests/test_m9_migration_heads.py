"""REQ-M9-00 guard: Alembic revision chain must have exactly one head.

Pure in-process Alembic metadata inspection via ScriptDirectory — no database
connection, no `op.*`, no engine. Fails if a duplicate/forked head (e.g. a
second `0008`) is ever reintroduced.
"""
import os

from alembic.config import Config
from alembic.script import ScriptDirectory


def _script_directory() -> ScriptDirectory:
    agentic_app_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    alembic_ini_path = os.path.join(agentic_app_root, "alembic.ini")
    config = Config(alembic_ini_path)
    config.set_main_option(
        "script_location", os.path.join(agentic_app_root, "migrations")
    )
    return ScriptDirectory.from_config(config)


def test_single_alembic_head() -> None:
    script_dir = _script_directory()
    heads = script_dir.get_heads()
    assert len(heads) == 1, f"expected exactly 1 head, got {heads}"


def test_head_is_0010() -> None:
    script_dir = _script_directory()
    heads = script_dir.get_heads()
    assert heads[0] == "0010"


def test_chain_links_eval_then_scim_then_audit() -> None:
    script_dir = _script_directory()
    heads = script_dir.get_heads()
    head_revision = script_dir.get_revision(heads[0])

    # 0010 (eval_records, milestone-9.3) chains off 0009 (SCIM, milestone-7.4
    # renumbered by REQ-M9-00) chains off 0008 (audit) chains off 0007 (RBAC).
    assert head_revision.down_revision == "0009"

    scim_revision = script_dir.get_revision(head_revision.down_revision)
    assert scim_revision.down_revision == "0008"

    audit_revision = script_dir.get_revision(scim_revision.down_revision)
    assert audit_revision.down_revision == "0007"
