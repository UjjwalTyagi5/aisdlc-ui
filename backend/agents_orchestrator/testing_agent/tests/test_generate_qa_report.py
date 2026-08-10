"""generate_qa_report: renders HTML always; PDF best-effort."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agents_orchestrator.testing_agent.Nodes.generate_qa_report import (
    generate_qa_report,
)


FIXTURE_DIR = Path(__file__).parent / "fixtures"


@pytest.mark.asyncio
async def test_generate_qa_report_html_always(tmp_path):
    fixture = json.loads((FIXTURE_DIR / "aggregated_results_fixture.json").read_text())
    state = {
        "session_id": "S-001",
        "aggregated_results": fixture,
        "language": "python",
        "output_dir": str(tmp_path),
    }
    delta = await generate_qa_report(state)
    html_path = delta["qa_report_html_path"]
    assert html_path is not None
    html = Path(html_path).read_text()
    assert "<table" in html
    assert "Test Strategy" in html or "Test Strategy" in html
    assert "/health" in html


@pytest.mark.asyncio
async def test_pdf_failure_does_not_break_html(tmp_path, monkeypatch):
    """Force weasyprint import to fail; HTML must still be produced."""
    import sys
    monkeypatch.setitem(sys.modules, "weasyprint", None)  # kill the import path

    fixture = json.loads((FIXTURE_DIR / "aggregated_results_fixture.json").read_text())
    state = {
        "session_id": "S-002",
        "aggregated_results": fixture,
        "language": "python",
        "output_dir": str(tmp_path),
    }
    delta = await generate_qa_report(state)
    assert delta["qa_report_html_path"] is not None
    assert delta["qa_report_pdf_path"] is None  # PDF skipped, HTML preserved
