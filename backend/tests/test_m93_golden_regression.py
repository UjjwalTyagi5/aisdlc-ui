"""M9.3 Plan 02 — CI-runnable golden regression test (REQ-M9-13).

Runs entirely offline: run_golden_set() in CI/default mode (no EVAL_LIVE_LLM)
scores the synthetic golden corpus deterministically; this test compares those
scores against tests/golden_sets/baselines.json + the configured threshold.

  - test_no_regression_passes: with the shipped synthetic corpus, every agent's
    score is >= baseline - threshold (passes today).
  - test_regression_below_threshold_fails: proves the tripwire actually trips —
    a degraded score (baseline - threshold - epsilon) is correctly flagged as
    a regression by the comparison logic.
  - test_synthetic_only: guards D-M9-03 — no golden case file may contain
    production-data markers (real email domains).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_GOLDEN_SETS_ROOT = Path(__file__).resolve().parent / "golden_sets"

_AGENT_DIRS: tuple[str, ...] = (
    "requirements",
    "design",
    "development",
    "testing",
    "deployment",
)

# D-M9-03 guard: production-data sentinel pattern. Synthetic corpus uses only
# fictional content (e.g. "Acme Widget"); real corporate email domains must
# never appear in version-controlled golden files.
_PRODUCTION_DATA_RE = re.compile(
    r"@(?!example\.(?:com|org|invalid)\b)[a-z0-9.\-]+\.(?:com|org|net|io)\b",
    re.IGNORECASE,
)


def _load_baselines() -> dict:
    with open(_GOLDEN_SETS_ROOT / "baselines.json", encoding="utf-8") as f:
        return json.load(f)


def _is_regression(score: float, baseline: float, threshold: float) -> bool:
    """Return True when `score` has dropped more than `threshold` below `baseline`."""
    return score < baseline - threshold


def test_no_regression_passes():
    from shared.eval.golden_runner import run_golden_set

    baselines = _load_baselines()
    threshold = baselines["threshold"]

    scores = run_golden_set()

    for agent_dir in _AGENT_DIRS:
        baseline = baselines[agent_dir]
        score = scores[agent_dir]
        assert not _is_regression(score, baseline, threshold), (
            f"{agent_dir}: score {score} regressed more than {threshold} "
            f"below baseline {baseline}"
        )


def test_regression_below_threshold_fails(monkeypatch):
    """Prove the tripwire trips: a degraded score must be flagged as a regression."""
    from shared.eval import golden_runner

    baselines = _load_baselines()
    threshold = baselines["threshold"]
    degraded_agent = "design"
    baseline = baselines[degraded_agent]

    real_run_golden_set = golden_runner.run_golden_set

    def _degraded_run_golden_set(agent_type=None, live=False):
        results = real_run_golden_set(agent_type=agent_type, live=live)
        # Drop the score well past the threshold — proves the comparison logic
        # correctly identifies a regression (not a no-op).
        results[degraded_agent] = max(0.0, baseline - threshold - 0.5)
        return results

    monkeypatch.setattr(golden_runner, "run_golden_set", _degraded_run_golden_set)

    scores = golden_runner.run_golden_set()
    score = scores[degraded_agent]

    assert _is_regression(score, baseline, threshold) is True


def test_synthetic_only():
    for agent_dir in _AGENT_DIRS:
        case_path = _GOLDEN_SETS_ROOT / agent_dir / "case_01.json"
        with open(case_path, encoding="utf-8") as f:
            content = f.read()

        match = _PRODUCTION_DATA_RE.search(content)
        assert match is None, (
            f"{case_path} contains a possible production-data marker: "
            f"{match.group(0) if match else None!r} (D-M9-03 violation)"
        )
