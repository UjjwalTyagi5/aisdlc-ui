"""REQ-M10-09: regression guard — the M5 mock/time-skip temporal logic still passes.

Tier: unit (subprocess-spawned pytest over a curated, environment-independent
subset of tests/temporal/ — no live Postgres, no live FastAPI server, no LLM).

milestone-10 generalized within-agent clarification into all 4 SDLC phases
(_run_phase_with_clarification, milestone-10.3-01) and added new M10 guards
(this plan, REQ-M10-07/08). This guard proves that change did NOT regress the
M5 inter-agent HITL / SLA / clarification machinery that runs entirely under
the Temporal time-skipping environment with mocked activities (no DB, no
live worker, no LLM).

Distinguishing regression from environment gaps (REQ-M10-09):
  `tests/temporal/` as a whole has TWO tiers:
    1. Mock/time-skip logic tests — pure SDLCWorkflow + signal-handler logic,
       run against `WorkflowEnvironment.start_time_skipping()` with mocked
       activities. These require ONLY `temporalio` to be installed.
    2. Live-integration tests — drive the workflow through a real FastAPI
       `signals.py` HTTP endpoint backed by Postgres
       (`test_hitl_approval_signal.py`, `test_hitl_rejection_signal.py`,
       `test_sla_timer_escalation.py`, `test_workflow_start_and_complete.py`,
       `test_artifact_chain_completeness.py`, `test_activity_idempotency.py`,
       `test_concurrent_workflows.py::test_concurrent_workflows`,
       `test_testing_agent_canary_e2e.py`). These fail in this dev session
       with `asyncpg.exceptions.InvalidPasswordError` /
       `401: Invalid token: Not enough segments` — pre-existing,
       environment-only failures (no live Postgres / no live API server in
       this session), documented in milestone-10.2-01-SUMMARY.md and
       milestone-10.3-01-SUMMARY.md. They are OUT OF SCOPE for this guard.

  This guard runs ONLY tier 1 — the mock/time-skip M5 + M10 clarification
  modules — which require nothing beyond `temporalio` being importable:
    - test_clarification_workflow.py    — M10.2 requirements clarification
      suspend/signal/resume reference loop (the M5-era SDLCWorkflow gates
      this loop wraps)
    - test_clarification_sla.py         — clarification SLA escalation
      while keeping the clarification gate open
    - test_clarification_all_phases.py  — M10.3 generalization to
      design/development/testing
    - test_clarification_protocol.py    — detect_clarification_need /
      _as_clarification_request pure-function tests
    - test_clarification_roundtrip.py   — ClarificationRequest/Answer
      DataConverter round-trip + Union ordering (the bug class fixed by
      milestone-10.3-01)
    - test_concurrent_workflows.py::test_concurrent_workflows_distinct_run_ids
      — concurrent run isolation (the mock/time-skip variant; the live-DB
      variant `test_concurrent_workflows` is excluded)
    - test_no_interrupt.py              — REQ-M10-07 structural guard (this
      plan)
    - test_within_agent_clarification_via_temporal.py — REQ-M10-08 E2E (this
      plan)

  Deferred (DLT-10 / live-only) tiers are excluded via `-k` so this guard
  does not require live Temporal worker processes:
    - "worker_restart"  — REQ-M10-08 live worker-kill proof (this plan,
      already @pytest.mark.skip)
    - "restart_resume"  — M5 test_workflow_restart_resume.py live tier

pytest exit codes:
  0 = all passed (or all passed+skipped)            -> OK
  1 = at least one test FAILED                       -> REGRESSION, must fail
  2 = internal/usage error                           -> REGRESSION, must fail
  3 = internal pytest error                          -> REGRESSION, must fail
  5 = no tests collected (e.g. temporalio absent)    -> ACCEPTABLE (whole
                                                         tier skipped, not a
                                                         regression)
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

# agentic_app root — two levels up from tests/temporal/.
_AGENTIC_APP_ROOT = Path(__file__).resolve().parents[2]

# Curated, environment-independent (mock/time-skip only — no Postgres, no
# live API server, no LLM) M5 + M10 modules. See module docstring for the
# rationale behind each inclusion/exclusion.
_GUARDED_MODULES = [
    "tests/temporal/test_clarification_workflow.py",
    "tests/temporal/test_clarification_sla.py",
    "tests/temporal/test_clarification_all_phases.py",
    "tests/temporal/test_clarification_protocol.py",
    "tests/temporal/test_clarification_roundtrip.py",
    "tests/temporal/test_concurrent_workflows.py",
    "tests/temporal/test_no_interrupt.py",
    "tests/temporal/test_within_agent_clarification_via_temporal.py",
]

# Acceptable pytest exit codes: 0 = all passed (skips OK), 5 = nothing
# collected (whole tier skipped, e.g. temporalio not installed).
_ACCEPTABLE_EXIT_CODES = {0, 5}


@pytest.mark.unit
def test_m5_regression_guard() -> None:
    """The M5 (+ M10) mock/time-skip temporal logic, minus deferred
    live-only worker-restart/live-DB tiers, must collect and run with no
    FAILURES or ERRORS (REQ-M10-09).

    Skips (temporalio absent) are tolerated — pytest exit 0 or 5. Exit 1/2/3
    (failures/errors/usage errors) fail this guard, surfacing a real
    regression in the M5 inter-agent HITL / SLA / clarification machinery.
    """
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        *_GUARDED_MODULES,
        "-q",
        "-k",
        "not worker_restart and not restart_resume",
        "--deselect",
        "tests/temporal/test_concurrent_workflows.py::test_concurrent_workflows",
    ]
    # --deselect excludes the live-DB `test_concurrent_workflows` test by
    # exact node ID (a `-k` substring filter would also match
    # `test_concurrent_workflows_distinct_run_ids`, which IS guarded).

    result = subprocess.run(
        cmd,
        cwd=str(_AGENTIC_APP_ROOT),
        env={**os.environ, "PYTHONPATH": "."},
        capture_output=True,
        text=True,
        timeout=600,
    )

    output_tail = "\n".join(result.stdout.splitlines()[-40:])
    stderr_tail = "\n".join(result.stderr.splitlines()[-20:])

    assert result.returncode in _ACCEPTABLE_EXIT_CODES, (
        f"M5 regression guard FAILED — inner pytest exit code "
        f"{result.returncode} (expected one of {_ACCEPTABLE_EXIT_CODES}). "
        f"This indicates a real failure/error in the M5 (or M10) "
        f"mock/time-skip clarification + HITL machinery, not an "
        f"environment-only skip.\n\n"
        f"--- stdout (tail) ---\n{output_tail}\n\n"
        f"--- stderr (tail) ---\n{stderr_tail}"
    )
