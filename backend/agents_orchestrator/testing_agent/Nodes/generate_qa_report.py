"""QA report renderer — Jinja2 to HTML (always); weasyprint to PDF (best-effort)."""
from __future__ import annotations

import datetime as dt
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

from jinja2 import Environment, FileSystemLoader, select_autoescape

from agents_orchestrator.testing_agent.config.session_state import SuperAgentState
from agents_orchestrator.testing_agent.config.shared import (
    BROADCASTING_AVAILABLE,
    blog,
    esett,
    get_session_id,
    get_user_id,
)
from agents_orchestrator.testing_agent.tools.coverage_html import (
    coverage_xml_to_html,
)

logger = logging.getLogger("testing_agent.qa_report")


TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
_ENV = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=select_autoescape(["html", "j2"]),
)


def _count_by_skill(test_sets: list, skill_name: str) -> int:
    for s in test_sets:
        if s.get("skill_name") == skill_name:
            return s.get("scenario_count") or 0
    return 0


async def generate_qa_report(state: SuperAgentState) -> Dict[str, Any]:
    blog("Generating QA report...")

    output_dir: Optional[str] = state.get("output_dir")
    if not output_dir and BROADCASTING_AVAILABLE:
        user_id = get_user_id()
        session_id = get_session_id()
        output_dir = f"{esett.FILES}/{user_id}/orchestrator/{session_id}/output"
    if not output_dir:
        logger.warning("generate_qa_report: no output_dir in state — skipping report")
        return {"qa_report_html_path": None, "qa_report_pdf_path": None}

    os.makedirs(output_dir, exist_ok=True)

    aggregated = state.get("aggregated_results") or {}
    test_sets = aggregated.get("generated_test_sets") or []

    coverage_html = ""
    work_dir = state.get("work_dir") or ""
    coverage_xml = os.path.join(work_dir, "reports", "coverage.xml") if work_dir else ""
    if coverage_xml and os.path.exists(coverage_xml):
        coverage_html = coverage_xml_to_html(coverage_xml)
    else:
        coverage_html = '<div class="coverage-unavailable">Coverage data unavailable.</div>'

    template = _ENV.get_template("qa_report.html.j2")
    report_session_id = state.get("session_id") or get_session_id() or "unknown"
    html = template.render(
        session_id=report_session_id,
        language=state.get("language") or "unknown",
        generated_at=dt.datetime.utcnow().isoformat() + "Z",
        unit_count=_count_by_skill(test_sets, "unit"),
        negative_edge_count=_count_by_skill(test_sets, "negative_edge"),
        smoke_count=_count_by_skill(test_sets, "smoke"),
        functional_api_count=_count_by_skill(test_sets, "functional_api"),
        functional_ui_count=_count_by_skill(test_sets, "functional_ui"),
        traceability={},  # Phase 10.1 stub — populated when req-mapping lands
        test_execution=aggregated.get("test_execution"),
        coverage_html=coverage_html,
        functional_results=aggregated.get("functional_results") or [],
        defect_log=aggregated.get("defect_log") or [],
        security_findings=aggregated.get("security_findings") or [],
        generated_test_sets=test_sets,
        # Phase 11.5 — new sections from shell-runtime skills
        mutation_results=aggregated.get("mutation_results"),
        dependency_vulns=aggregated.get("dependency_vulns") or [],
        artifact_files=sorted(os.listdir(output_dir)) if os.path.isdir(output_dir) else [],
    )

    html_path = os.path.join(output_dir, "qa_report.html")
    Path(html_path).write_text(html, encoding="utf-8")
    blog(f"QA report HTML: {html_path}")

    pdf_path: Optional[str] = None
    try:
        from weasyprint import HTML
        if HTML is None:
            raise RuntimeError("weasyprint module is None (test injection)")
        pdf_path = os.path.join(output_dir, "qa_report.pdf")
        HTML(string=html).write_pdf(pdf_path)
        blog(f"QA report PDF: {pdf_path}")
    except Exception as exc:
        logger.warning(f"QA report PDF render failed (non-fatal): {exc}")
        pdf_path = None

    return {"qa_report_html_path": html_path, "qa_report_pdf_path": pdf_path, "output_dir": output_dir}
