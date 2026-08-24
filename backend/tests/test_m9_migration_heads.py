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


# NOTE — test_head_is_0010 and test_chain_links_eval_then_scim_then_audit removed:
# both pinned the *milestone-9.3-era* head/chain — "0010" was the newest revision
# only at that milestone's own PR. The chain has since grown past it (27 migrations
# at last count, several with merge-heads revisions resolving parallel branches),
# and revision "0010" in this repo's actual history is 0010_governance_requests, not
# the "0010_eval_records" these tests assumed — eval-records support was folded into
# 0001_baseline.py instead of landing as its own migration. Point-in-time gates like
# these go stale by design as development continues past their milestone; the
# durable invariant they were protecting — exactly one Alembic head, no orphaned
# branches — is what test_single_alembic_head above still checks, unpinned to any
# specific revision number.
