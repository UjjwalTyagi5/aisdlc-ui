"""Static audit test for REQ-M7-07: no bare AsyncSessionFactory() in background paths.

This test reads each audited source file and asserts that `AsyncSessionFactory()`
does NOT appear as a direct call outside the explicitly-allowed system-op files.

ALLOWLIST (legitimately bare AsyncSessionFactory() — not migrated):
  shared/db.py              — the factory definition itself; get_db_session* wrap it
  scripts/seed_e2e_fixtures.py — local seed script run as superuser DSN

Every other file under agentic_app that imports AsyncSessionFactory must use it
only inside get_db_session_* wrapper functions, never as a direct call in
background paths.

Audit covers the call sites enumerated in the plan callsite_checklist:
  2  process_api.py (_handle_artifact_ready)
  4  workflows/activities/_base.py (check_existing_artifact)
  6  config/connectors/jira.py (audit_emitter)
  7  config/connectors/slack.py (audit_emitter)
  8  config/connectors/github_issues.py (audit_emitter)
  9  config/connectors/azure_repos.py (audit_emitter)
  10 config/connectors/azure_devops.py (audit_emitter)

And the deviation call site resolved at execute-time:
  D1 shared/services/artifact_service.py (persist_artifact — background pipeline path)

REMOVED FROM THE AUDIT — gone with Temporal, not renamed:
  1  webhooks/consumer.py (_ensure_webhook_run_row) — WebhookConsumer no longer exists
     (see shared/tests/test_m6_verification.py's module docstring: "WebhookConsumer is
     gone with Temporal: it existed to turn a stream event into a workflow, and there
     is no engine to start").
  3  workflows/activities/sync_status_activity.py — Temporal @activity.defn wrapper;
     gone per workflows/activities/__init__.py's own docstring. Status sync is now the
     bridge in workflows/activities/pipeline_session.py.
  5  workflows/activities/emit_escalation_activity.py — same removal; gate-pending /
     escalation notification now lives in shared/services/orchestrator/gate_routing.py
     and shared/governance/routing.py (notify_gate_pending), not a Temporal activity.
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
    "process_api.py",                                 # site 2
    "workflows/activities/_base.py",                  # site 4
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
    "workflows/activities/_base.py",
    "config/connectors/jira.py",
    "config/connectors/slack.py",
    "config/connectors/github_issues.py",
    "config/connectors/azure_repos.py",
    "config/connectors/azure_devops.py",
    "shared/services/artifact_service.py",
]


# Connectors no longer call get_db_session_for_tenant directly for audit writes — all
# five route audit_emitter() through shared/audit/service.py's audit_service.emit(),
# which itself calls get_db_session_for_tenant(str(payload.tenant_id)) (see
# shared/audit/service.py). One tenant-scoped write path shared by every connector,
# rather than five copies of the same call — the tenant-scoping guarantee this test
# checks for is still satisfied, just centralized one level down.
_AUDIT_SERVICE_INDIRECT_USERS = {
    "config/connectors/jira.py",
    "config/connectors/slack.py",
    "config/connectors/github_issues.py",
    "config/connectors/azure_repos.py",
    "config/connectors/azure_devops.py",
}


def test_migrated_files_use_tenant_session_api():
    """Assert that key migrated files use the tenant-scoped session — directly, or via
    shared.audit.service.audit_service for the connector audit_emitter call sites.

    This is a positive-contract check: each migrated write path must actively
    reference the tenant-scoped session function, not just avoid AsyncSessionFactory().
    """
    missing: list[str] = []
    for rel_path in _TENANT_SESSION_USERS:
        source = _read(rel_path)
        if "get_db_session_for_tenant" in source:
            continue
        if rel_path in _AUDIT_SERVICE_INDIRECT_USERS and "audit_service" in source:
            continue
        missing.append(rel_path)

    assert not missing, (
        "REQ-M7-07 positive contract: the following migrated files do not reference "
        "get_db_session_for_tenant (directly, or via audit_service for connector "
        "audit_emitter call sites) — verify the migration was applied correctly:\n"
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


# NOTE — test_webhook_consumer_preserves_best_effort and test_sdlcworkflow_threads_
# tenant_id removed: both read files (webhooks/consumer.py, workflows/sdlc_workflow.py)
# that no longer exist — Temporal WorkflowDefn/activity plumbing retired along with
# WebhookConsumer (see the module docstring's "REMOVED FROM THE AUDIT" section above).
# tenant_id threading for the conversational path that replaced them is covered by
# tests/test_m74_byok.py's test_tenant_id_threaded_through_pipeline_session.
