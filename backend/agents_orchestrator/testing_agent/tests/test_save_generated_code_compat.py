"""Regression test: save_generated_code tolerates fan-out state shape.

Phase 10 introduced dispatch_test_types which writes per-skill files directly
and sets state["generated_test_sets"] (list of dicts). It does NOT set
state["generated_test_code"] (the legacy single-string shape). The graph
still routes through save_generated_code after dispatch_test_types, so that
node must NOT KeyError when the legacy key is missing.

Live bug seen: 500 from /testing_orchestrator/chat/ on 2026-05-05 because
save_generated_code did `state['generated_test_code']` (subscript) on missing
key after the fan-out completed. Fix: use .get() and no-op when missing.
"""
from __future__ import annotations

import os
import tempfile

import pytest

from agents_orchestrator.testing_agent.Nodes.generate import save_generated_code


@pytest.mark.asyncio
async def test_save_generated_code_no_ops_when_fan_out_set_files():
    """Fan-out path: state has generated_test_sets but no generated_test_code."""
    with tempfile.TemporaryDirectory() as work_dir:
        # Skills wrote two files
        f1 = os.path.join(work_dir, "test_generated_unit.py")
        f2 = os.path.join(work_dir, "test_generated_negative_edge.py")
        for p in (f1, f2):
            with open(p, "w") as f:
                f.write("# placeholder")

        state = {
            "work_dir": work_dir,
            "language": "python",
            "generated_test_sets": [
                {"skill_name": "unit", "test_file_path": f1, "test_framework": "pytest", "scenario_count": 5},
                {"skill_name": "negative_edge", "test_file_path": f2, "test_framework": "pytest", "scenario_count": 3},
            ],
            # NO generated_test_code key
        }
        delta = await save_generated_code(state)
        assert delta == {}
        # The legacy fallback file was NOT created (no-op behavior)
        assert not os.path.exists(os.path.join(work_dir, "test_generated_by_agent.py"))


@pytest.mark.asyncio
async def test_save_generated_code_legacy_path_still_works():
    """Legacy path: generated_test_code present → still writes the single file."""
    with tempfile.TemporaryDirectory() as work_dir:
        state = {
            "work_dir": work_dir,
            "language": "python",
            "generated_test_code": "import pytest\ndef test_x(): assert True\n",
        }
        await save_generated_code(state)
        legacy = os.path.join(work_dir, "test_generated_by_agent.py")
        assert os.path.isfile(legacy)
        with open(legacy) as f:
            assert "test_x" in f.read()


@pytest.mark.asyncio
async def test_save_generated_code_legacy_dotnet_path():
    """Dotnet legacy path: generated_test_code present → writes tests/GeneratedTests.cs."""
    with tempfile.TemporaryDirectory() as work_dir:
        state = {
            "work_dir": work_dir,
            "language": "dotnet",
            "generated_test_code": "using Xunit;\npublic class T {}\n",
        }
        await save_generated_code(state)
        legacy = os.path.join(work_dir, "tests", "GeneratedTests.cs")
        assert os.path.isfile(legacy)


@pytest.mark.asyncio
async def test_save_generated_code_neither_present_warns_and_continues():
    """No code AND no fan-out sets → warn but don't crash. State delta empty."""
    with tempfile.TemporaryDirectory() as work_dir:
        state = {
            "work_dir": work_dir,
            "language": "python",
            # No generated_test_code, no generated_test_sets
        }
        delta = await save_generated_code(state)
        assert delta == {}
