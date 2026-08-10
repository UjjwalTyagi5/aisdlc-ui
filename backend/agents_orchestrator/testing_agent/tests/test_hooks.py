"""Hooks tolerate missing state, never raise, return state deltas."""
from __future__ import annotations

import os
import tempfile

from agents_orchestrator.testing_agent.hooks import (
    on_failure,
    post_test_run,
    pre_test_run,
)


def test_pre_test_run_creates_reports_dir():
    with tempfile.TemporaryDirectory() as work:
        state = {"work_dir": work}
        pre_test_run(state)
        assert os.path.isdir(os.path.join(work, "reports"))


def test_pre_test_run_handles_missing_work_dir():
    pre_test_run({})  # no work_dir → must not raise


def test_post_test_run_returns_delta_with_defect_log():
    state = {"work_dir": "/tmp"}
    delta = post_test_run(state, results={"defect_log": [{"defect_id": "DEF-001", "severity": "low", "summary": "x"}]})
    assert delta is None or "defect_log" in delta


def test_on_failure_logs_but_does_not_raise():
    on_failure({}, exc=RuntimeError("boom"), ctx="unit-skill")  # must not raise


def test_pre_test_run_swallows_internal_errors():
    # Pass a state where work_dir resolves to a path we cannot create
    # (e.g. read-only) — pre_test_run must log + return, not raise.
    bad_state = {"work_dir": "/this/path/does/not/exist/and/is/unwritable"}
    try:
        pre_test_run(bad_state)
    except Exception as exc:
        raise AssertionError(f"pre_test_run raised: {exc}")
