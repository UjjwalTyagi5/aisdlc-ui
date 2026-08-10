"""Golden-set runner for the M9.3 eval harness (REQ-M9-12/13).

Scores all 5 SDLC agents (requirements, design, development, testing, deployment —
the 5 agents under agentic_app/agents_orchestrator/, per CLAUDE.md) against
version-controlled SYNTHETIC golden cases in tests/golden_sets/<agent_dir>/case_01.json
(D-M9-03 — synthetic-only, no production data).

Two modes:
  - CI / offline (default, live=False): scores each case's "reference_actual"
    (a canned, deterministic output baked into the case file) against "expected"
    via shared.eval.scoring.score_output. No LLM call, no network — fully
    deterministic for CI (REQ-M9-13).
  - live (live=True, gated behind EVAL_LIVE_LLM=true): would invoke the real
    agent graphs and score their actual output. NOT implemented here — staging/
    live baselines are deferred to DLT-9 (needs live LLMs). Calling run_golden_set
    with live=True without EVAL_LIVE_LLM=true raises RuntimeError so CI can never
    accidentally take the live path (T-9.3-08).

Public API:
  run_golden_set(agent_type=None, live=False) -> dict[str, float]
      Returns {agent_type: score} for all requested agents (all 5 if agent_type
      is None), using the case file's own "agent_type" field as the dict key.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from shared.eval.scoring import score_output

# Maps the golden_sets/ directory name to the case file's declared agent_type.
# "design" directory's case_01.json declares agent_type "design_architecture"
# (the scoring contract's section-presence strategy key, see scoring.py).
_GOLDEN_SET_DIRS: tuple[str, ...] = (
    "requirements",
    "design",
    "development",
    "testing",
    "deployment",
)

_GOLDEN_SETS_ROOT = Path(__file__).resolve().parents[2] / "tests" / "golden_sets"


def _load_case(agent_dir: str) -> dict:
    case_path = _GOLDEN_SETS_ROOT / agent_dir / "case_01.json"
    with open(case_path, encoding="utf-8") as f:
        return json.load(f)


def run_golden_set(
    agent_type: Optional[str] = None,
    live: bool = False,
) -> dict[str, float]:
    """Score golden-set cases for one or all 5 agents.

    Args:
        agent_type: when provided, scores only the matching golden_sets/<agent_dir>
            (e.g. "design" for tests/golden_sets/design/case_01.json). When None,
            scores all 5 agents.
        live: when True, requires EVAL_LIVE_LLM=true in the environment and
            would invoke the real agent graphs (DLT-9, not implemented).
            CI must never set EVAL_LIVE_LLM — defaults to offline/deterministic.

    Returns:
        {agent_dir: score} keyed by the golden_sets/ directory name (matches
        the 5 agents_orchestrator/ agent names per CLAUDE.md: "requirements",
        "design", "development", "testing", "deployment"). The case file's own
        "agent_type" field (e.g. "design_architecture" for the design case) is
        used internally to select the score_output scoring strategy.

    Raises:
        RuntimeError: if live=True and EVAL_LIVE_LLM is not "true" (T-9.3-08).
    """
    if live:
        if os.environ.get("EVAL_LIVE_LLM", "").lower() != "true":
            raise RuntimeError(
                "run_golden_set(live=True) requires EVAL_LIVE_LLM=true in the "
                "environment. Live/staging baselines are deferred to DLT-9 — "
                "do not enable this in CI (T-9.3-08)."
            )
        raise NotImplementedError(
            "Live agent-graph golden runs are deferred to DLT-9 "
            "(needs live LLMs and staging baselines)."
        )

    results: dict[str, float] = {}
    for agent_dir in _GOLDEN_SET_DIRS:
        if agent_type is not None and agent_dir != agent_type:
            continue

        case = _load_case(agent_dir)
        eval_signals = score_output(
            case["agent_type"], case["reference_actual"], case["expected"]
        )
        results[agent_dir] = eval_signals.score

    return results
