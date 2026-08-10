"""aggregate_test_results: merges all sources into AggregatedResults; builds defect_log."""
from __future__ import annotations

import pytest

from agents_orchestrator.testing_agent.Nodes.aggregate_test_results import (
    aggregate_test_results,
)


@pytest.mark.asyncio
async def test_merges_unit_and_functional_results():
    state = {
        "generated_test_sets": [
            {"skill_name": "unit", "test_file_path": "/x/test_unit.py", "test_framework": "pytest", "scenario_count": 5},
            {"skill_name": "functional_api", "test_file_path": "/x/test_func.py", "test_framework": "pytest", "scenario_count": 3},
        ],
        "skill_failures": [],
        "test_execution_summary": "5/5 passed",
        "ui_test_results": [{"id": 1, "status": "Pass"}],
    }
    delta = await aggregate_test_results(state)
    agg = delta["aggregated_results"]
    assert len(agg["generated_test_sets"]) == 2
    assert agg["ui_test_results"] == [{"id": 1, "status": "Pass"}]


@pytest.mark.asyncio
async def test_defect_log_built_from_skill_failures():
    state = {
        "generated_test_sets": [],
        "skill_failures": ["functional_api: TimeoutError: connection timed out"],
    }
    delta = await aggregate_test_results(state)
    log = delta.get("defect_log", [])
    assert any("functional_api" in d.get("summary", "") for d in log)


@pytest.mark.asyncio
async def test_handles_empty_state():
    delta = await aggregate_test_results({})
    assert "aggregated_results" in delta
