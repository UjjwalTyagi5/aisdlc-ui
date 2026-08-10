"""Regression tests for I3: sticky `selected_test_types` re-triggering a full run.

Root cause: classify_intent reads `selected_test_types` from the checkpoint first
(falling back to prompt inference). Once a run has set it, every later turn kept
re-entering the run-launch branch — so a follow-up question like "what was the
coverage?" silently re-ran the whole suite instead of being answered by the
follow-up handler.

Fix: a guard immediately before the run-launch branch detects when the type came
only from the checkpoint (this turn's prompt names no type) AND a prior run
already completed AND there's no explicit new target URL — then it clears the
sticky scope and routes to "follow_up_query" instead.
"""
from __future__ import annotations

import pytest

from agents_orchestrator.testing_agent.Nodes.ingest_input import classify_intent


@pytest.mark.asyncio
async def test_followup_after_completed_run_routes_to_followup():
    result = await classify_intent({
        "user_prompt": "what was the coverage?",
        "selected_test_types": ["unit"],
        "test_run_attempted": True,
    })

    assert result["classified_intent"] == "follow_up_query"
    assert result["selected_test_types"] is None


@pytest.mark.asyncio
async def test_fresh_selection_still_runs():
    result = await classify_intent({
        "user_prompt": "run unit testing",
    })

    # No prior-run markers present, so the guard must not fire — either a real
    # run intent is returned, or (if there's no code target yet) the "needs
    # code" greeting is returned. Either way it must NOT be misclassified as a
    # follow-up.
    assert result["classified_intent"] != "follow_up_query"


@pytest.mark.asyncio
async def test_explicit_new_type_after_run_reruns():
    result = await classify_intent({
        "user_prompt": "now run api testing https://api.example.com",
        "selected_test_types": ["unit"],
        "test_run_attempted": True,
    })

    # The prompt itself names a test type ("api") and supplies a target URL,
    # so the guard must not fire even though a prior run completed.
    assert result["classified_intent"] != "follow_up_query"
    # Regression (fix b): the stale checkpointed "unit" must not win — the
    # prompt-named "api" type resolves to the API-testing route, not a unit run.
    assert result["classified_intent"] == "api_ui_only"
    assert result.get("selected_test_types") == ["api"]


@pytest.mark.asyncio
async def test_prompt_named_type_overrides_stale_checkpoint_type():
    """Fix (b): a checkpointed 'unit' selection from a prior run must not win
    over a test type explicitly named in the CURRENT prompt, even without a
    URL — the resolved `selected_test_types` should reflect the new prompt,
    and the router must not silently stay on a unit-test path."""
    result = await classify_intent({
        "user_prompt": "now run api testing",
        "selected_test_types": ["unit"],
        "test_run_attempted": True,
    })

    assert result["classified_intent"] != "follow_up_query"
    assert result["classified_intent"] not in {"full_test", "single_file_test"}
    assert result.get("selected_test_types") == ["api"]


@pytest.mark.asyncio
async def test_no_type_in_prompt_still_uses_checkpointed_type():
    """A prompt that names no test type (and isn't a bare URL/approval) must
    still fall back to the checkpointed `selected_test_types` — fix (b) only
    changes precedence when the CURRENT prompt names a type."""
    result = await classify_intent({
        "user_prompt": "please continue",
        "selected_test_types": ["unit"],
    })

    assert result.get("selected_test_types") == ["unit"] or "unit" in (result.get("selected_test_types") or [])
    assert result["classified_intent"] != "follow_up_query"
