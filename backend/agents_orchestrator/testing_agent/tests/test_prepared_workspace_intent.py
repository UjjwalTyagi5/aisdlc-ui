"""setup_workspace treats an existing `work_dir` as the highest-priority source of
code — it is the Copilot's shared run workspace, cloned once for the whole pipeline.
The intent gate did not agree: with unit selected and no upload or clone_target it
answered "Unit testing needs code to test" and never reached setup_workspace, so an
orchestrator handoff that passed the workspace instead of a repo/branch did nothing
with a checkout sitting right there.
"""
import asyncio

import pytest

from agents_orchestrator.testing_agent.Nodes import ingest_input


def _classify(state):
    fn = getattr(ingest_input, "classify_intent", None) or getattr(ingest_input, "ingest_input")
    out = fn(state)
    if asyncio.iscoroutine(out):
        out = asyncio.run(out)
    return out


def test_prepared_work_dir_counts_as_code_to_test(tmp_path):
    (tmp_path / "app.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")

    out = _classify({
        "user_prompt": "run unit tests",
        "selected_test_types": ["unit"],
        "work_dir": str(tmp_path),
    })

    assert out.get("classified_intent") == "full_test"


def test_no_code_target_at_all_still_asks_for_code():
    out = _classify({"user_prompt": "run unit tests", "selected_test_types": ["unit"]})

    assert out.get("classified_intent") == "greeting"
    assert "needs code to test" in (out.get("final_user_message") or "")


def test_stale_work_dir_does_not_start_a_run_against_a_missing_directory(tmp_path):
    """A path that no longer exists must fall through to the prompt, not be trusted —
    the same guard setup_workspace applies before reusing one."""
    out = _classify({
        "user_prompt": "run unit tests",
        "selected_test_types": ["unit"],
        "work_dir": str(tmp_path / "was-cleaned-up"),
    })

    assert out.get("classified_intent") == "greeting"
    assert "needs code to test" in (out.get("final_user_message") or "")
