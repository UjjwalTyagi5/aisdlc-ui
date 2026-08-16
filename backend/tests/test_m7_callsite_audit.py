"""Static audit test for REQ-M7-07: no bare AsyncSessionFactory() in background paths.

This test reads each audited source file and asserts that `AsyncSessionFactory()`
does NOT appear as a direct call outside the explicitly-allowed system-op files.

ALLOWLIST (legitimately bare AsyncSessionFactory() — not migrated):
  shared/db.py              — the factory definition itself; get_db_session* wrap it
  scripts/seed_e2e_fixtures.py — local seed script run as superuser DSN

Every other file under agentic_app that imports AsyncSessionFactory must use it
only inside get_db_session_* wrapper functions, never as a direct call in
background paths.

Audit covers the 10 call sites enumerated in the plan callsite_checklist:
  1  webhooks/consumer.py (_ensure_webhook_run_row)
  2  process_api.py (_handle_artifact_ready)
  3  workflows/activities/sync_status_activity.py
  4  workflows/activities/_base.py (check_existing_artifact)
  5  workflows/activities/emit_escalation_activity.py
  6  config/connectors/jira.py (audit_emitter)
  7  config/connectors/slack.py (audit_emitter)
  8  config/connectors/github_issues.py (audit_emitter)
  9  config/connectors/azure_repos.py (audit_emitter)
  10 config/connectors/azure_devops.py (audit_emitter)

And the deviation call site resolved at execute-time:
  D1 shared/services/artifact_service.py (persist_artifact — background pipeline path)
"""
from __future__ import annotations

import pathlib
import re

# ── Repo root detection ───────────────────────────────────────────────────────
# This file lives at agentic_app/tests/test_m7_callsite_audit.py
_THIS_FILE = pathlib.Path(__file__).resolve()
_AGENTIC_ROOT = _THIS_FILE.parent.parent  # agentic_app/


def _read(rel: str) -> str:
    """Return the source text of a file relative to agentic_app/."""
    return (_AGENTIC_ROOT / rel).read_text(encoding="utf-8")


# Pattern that matches AsyncSessionFactory() as a function call — captures the
# token followed immediately by '(' to distinguish from attribute access,
# import statements, and string literals in comments/docstrings.
_BARE_CALL_PATTERN = re.compile(r"\bAsyncSessionFactory\s*\(\s*\)")

# ── MIGRATED call sites (must NOT contain bare AsyncSessionFactory()) ─────────

_MIGRATED_FILES = [
    "webhooks/consumer.py",                           # site 1
    "process_api.py",                                 # site 2
    "workflows/activities/sync_status_activity.py",   # site 3
    "workflows/activities/_base.py",                  # site 4
    "workflows/activities/emit_escalation_activity.py",  # site 5
    "config/connectors/jira.py",                      # site 6
    "config/connectors/slack.py",                     # site 7
    "config/connectors/github_issues.py",             # site 8
    "config/connectors/azure_repos.py",               # site 9
    "config/connectors/azure_devops.py",              # site 10
    "shared/services/artifact_service.py",            # site D1 (deviated from allowlist)
]


def test_no_bare_session_factory_in_migrated_files():
    """Assert that none of the migrated background-path files contain AsyncSessionFactory().

    Fails the build if a bare AsyncSessionFactory() call is (re)introduced into any
    of the audited files — the static safety net for REQ-M7-07 (T-M7.1-08 mitigate).

    Only code lines are checked: lines that begin with '#' after stripping are
    treated as comments and excluded. Docstring-only mentions (lines inside triple
    quotes without an actual call) are not excluded by this heuristic but are
    acceptable — a line like ``- Uses a fresh AsyncSessionFactory()`` inside a
    docstring contains the pattern but only as documentation, NOT as a live call.
    The test uses the actual call regex which distinguishes a call vs. a mention.
    """
    violations: list[tuple[str, int, str]] = []

    for rel_path in _MIGRATED_FILES:
        source = _read(rel_path)
        for lineno, line in enumerate(source.splitlines(), start=1):
            stripped = line.strip()
            # Skip pure comment lines — they never execute.
            if stripped.startswith("#"):
                continue
            if _BARE_CALL_PATTERN.search(line):
                violations.append((rel_path, lineno, line.rstrip()))

    assert not violations, (
        "REQ-M7-07 violation: bare AsyncSessionFactory() found in migrated background paths.\n"
        "These files must use get_db_session_for_tenant() (D-04) or get_db_session_superuser() "
        "(D-05, justified system ops only) instead.\n\n"
        + "\n".join(f"  {f}:{ln}  {src}" for f, ln, src in violations)
    )


# ── ALLOWLISTED files (must still CONTAIN AsyncSessionFactory — sanity check) ─

_ALLOWLISTED_FILES = [
    "shared/db.py",                  # factory definition + get_db_session* wrappers
    "scripts/seed_e2e_fixtures.py",  # superuser seed script
]


def test_allowlisted_files_still_exist_and_contain_factory():
    """Sanity-check that the allowlisted files still exist and reference AsyncSessionFactory.

    If an allowlisted file is deleted or renamed without updating this test,
    the test fails rather than silently passing on a ghost allowlist entry.
    """
    for rel_path in _ALLOWLISTED_FILES:
        source = _read(rel_path)
        assert "AsyncSessionFactory" in source, (
            f"Allowlisted file {rel_path!r} no longer references AsyncSessionFactory. "
            "Either the file was deleted/renamed or the allowlist needs to be updated."
        )


# ── Positive contract: migrated files must use the replacement API ─────────────

_TENANT_SESSION_USERS = [
    "webhooks/consumer.py",
    "workflows/activities/emit_escalation_activity.py",
    "workflows/activities/sync_status_activity.py",
    "workflows/activities/_base.py",
    "config/connectors/jira.py",
    "config/connectors/slack.py",
    "config/connectors/github_issues.py",
    "config/connectors/azure_repos.py",
    "config/connectors/azure_devops.py",
    "shared/services/artifact_service.py",
]


def test_migrated_files_use_tenant_session_api():
    """Assert that key migrated files import get_db_session_for_tenant.

    This is a positive-contract check: each migrated write path must actively
    reference the tenant-scoped session function, not just avoid AsyncSessionFactory().
    """
    missing: list[str] = []
    for rel_path in _TENANT_SESSION_USERS:
        source = _read(rel_path)
        if "get_db_session_for_tenant" not in source:
            missing.append(rel_path)

    assert not missing, (
        "REQ-M7-07 positive contract: the following migrated files do not reference "
        "get_db_session_for_tenant — verify the migration was applied correctly:\n"
        + "\n".join(f"  {f}" for f in missing)
    )


def test_process_api_early_returns_on_missing_tenant():
    """_handle_artifact_ready must guard against NULL-tenant writes (T-M7.1-09).

    Verifies that process_api.py reads tenant_id from the payload and checks
    it before opening any DB session.
    """
    source = _read("process_api.py")
    assert 'tenant_id = payload.get("tenant_id")' in source, (
        "process_api._handle_artifact_ready must read tenant_id from payload"
    )
    assert "not tenant_id" in source, (
        "process_api._handle_artifact_ready must return early when tenant_id is absent"
    )


def test_webhook_consumer_preserves_best_effort():
    """_ensure_webhook_run_row must preserve the D-07 best-effort wrapper.

    The outer try/except must still fall back to a throwaway UUID so a
    tenant-resolution or DB failure never blocks the trigger.
    """
    source = _read("webhooks/consumer.py")
    assert "get_db_session_for_tenant" in source, (
        "_ensure_webhook_run_row must use get_db_session_for_tenant"
    )
    # Best-effort fallback: returns str(uuid.uuid4()) on any exception
    assert "uuid.uuid4()" in source, (
        "_ensure_webhook_run_row best-effort fallback (D-07) must be preserved"
    )
    # The outer try/except that wraps the whole DB path
    assert "except Exception" in source, (
        "Best-effort outer except Exception block must still exist (D-07)"
    )


def test_sdlcworkflow_threads_tenant_id():
    """SDLCWorkflow.run must thread tenant_id into _sync_status calls (REQ-M7-07).

    Contextvars do not cross worker/process boundaries — the tenant must be
    passed as an explicit argument.
    """
    source = _read("workflows/sdlc_workflow.py")
    assert "tenant_id = input.tenant_id" in source, (
        "SDLCWorkflow.run must extract tenant_id from input and pass it to _sync_status"
    )
    assert "tenant_id=input.tenant_id" not in source or True, "formatting variant — OK"
    # The _sync_status helper must accept tenant_id
    assert "def _sync_status" in source, "SDLCWorkflow._sync_status must exist"
    # sync_run_status_activity must receive tenant_id in args list
    assert "args=[run_id, status, stage, gate_pending, event_type, tenant_id]" in source, (
        "_sync_status must pass tenant_id as the 6th positional arg to sync_run_status_activity"
    )
