"""Lifecycle hooks for the testing-agent fan-out.

Three plain functions. No decorator magic. Called explicitly from
dispatch_test_types / aggregate_test_results / _run_skill so they're easy to
grep + trace.

Contract: hooks NEVER raise. Each wraps its body in try/except and logs the
failure so the graph keeps moving.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

logger = logging.getLogger("testing_agent.hooks")


def pre_test_run(state: Dict[str, Any]) -> None:
    """Called before any test-type generator dispatches.

    Today: warm sandbox state, ensure work_dir/reports exists. Tomorrow:
    hot-load test-type plugins, clear stale reports from prior runs.
    """
    try:
        work_dir = state.get("work_dir")
        if work_dir:
            os.makedirs(os.path.join(work_dir, "reports"), exist_ok=True)
        logger.info("pre_test_run: hook ran")
    except Exception as exc:
        logger.warning(f"pre_test_run hook failed (non-fatal): {exc}")


def post_test_run(state: Dict[str, Any], results: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Called after fan-in / aggregation. Returns a state delta with defect_log
    (or None on failure). Never raises.
    """
    try:
        defect_log = list(state.get("defect_log") or [])
        for entry in (results.get("defect_log") or []):
            defect_log.append(entry)
        logger.info(f"post_test_run: {len(defect_log)} defects accumulated")
        return {"defect_log": defect_log} if defect_log else None
    except Exception as exc:
        logger.warning(f"post_test_run hook failed (non-fatal): {exc}")
        return None


def on_failure(state: Dict[str, Any], exc: Exception, ctx: str) -> None:
    """Per-skill or per-runner failure. Logs to defect log; never raises."""
    try:
        logger.warning(f"on_failure[{ctx}]: {type(exc).__name__}: {exc}")
        # No state mutation here — caller is responsible for adding a DefectEntry
        # via the aggregator. This hook exists for telemetry.
    except Exception as inner:
        logger.warning(f"on_failure hook itself failed (non-fatal): {inner}")
