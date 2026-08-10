"""Snapshot test: QA report HTML shape stable across template edits."""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import pytest


FIXTURE_DIR = Path(__file__).parent / "fixtures"


def _normalize(html: str) -> str:
    """Strip non-deterministic timestamps before diffing."""
    return re.sub(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.?\d*Z", "<TS>", html)


@pytest.mark.asyncio
async def test_qa_report_matches_snapshot(tmp_path):
    from agents_orchestrator.testing_agent.Nodes.generate_qa_report import generate_qa_report

    fixture = json.loads((FIXTURE_DIR / "aggregated_results_fixture.json").read_text())
    state = {
        "session_id": "SNAP-001",
        "aggregated_results": fixture,
        "language": "python",
        "output_dir": str(tmp_path),
    }
    delta = await generate_qa_report(state)
    actual = Path(delta["qa_report_html_path"]).read_text()
    expected = (FIXTURE_DIR / "qa_report.expected.html").read_text()

    # Compare shape — non-deterministic fields normalized
    assert _normalize(actual) == _normalize(expected), (
        "QA report HTML diverged from snapshot. If this is intentional, "
        "regenerate qa_report.expected.html with the steps in Task 21."
    )
