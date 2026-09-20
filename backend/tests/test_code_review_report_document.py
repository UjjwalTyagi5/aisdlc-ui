"""The Code Review Report: the review of a change, and nothing else.

IT USED TO BE TWO REPORTS IN ONE. It carried the scanners' output — secrets, dependency
vulnerabilities, an SBOM — which is what the SECURITY agent produces (PRD 21.5 owns the
scanning stack, the SBOM and the sign-off), so both pages showed the same SBOM and neither
was the answer. This one answers PRD 21.4: was the whole change read, does it meet the
APPROVED requirements, does it follow the APPROVED design, what is wrong and where, and
should it merge.

What must hold in the document a reader acts on:
- no scan, no SBOM, no security verdict — security appears only as a reviewer's finding;
- tables are real tables (their rows consecutive), on the designed canvas;
- a whole-branch review states how many files were read of how many;
- the report is filed as a draft and linked from the saved review — or the review says
  why there is no report.
"""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.unit


def _artifact(**over):
    art = {
        "context": {"repo_name": "QuickLink", "ado_project": "QuickLink", "mode": "repo",
                    "source_branch": "feature/x", "base_branch": "", "head_sha": "082f91e49bf0"},
        "summary": "## Overview\nA small service.",
        "merge_recommendation": "request_changes",
        "findings": [
            {"id": "F-002", "severity": "medium", "category": "logic_error", "file": "src/a.js", "line": 3,
             "description": "Race.", "recommendation": "Add a constraint."},
            {"id": "F-001", "severity": "high", "category": "security", "file": "src/b.js", "line": 9,
             "description": "Open redirect.", "recommendation": "Allow http(s) only."},
        ],
        "requirements_coverage": [], "design_conformance": [],
        "scope": {"mode": "repo", "files_total": 20, "reviewable_files": 14, "lines_total": 325,
                  "files_read": ["src/a.js"], "reviewable_files_read": 11, "not_read": ["tests/x.test.js"],
                  "languages": {"JavaScript": 8}},
    }
    art.update(over)
    return art


def test_tables_are_single_blocks_and_findings_are_ordered_by_severity():
    from agents_orchestrator.code_review_agent.review_document import review_markdown

    md = review_markdown(_artifact())
    findings = md.split("## Findings", 1)[1].split("## Requirements coverage", 1)[0].strip()
    rows = findings.splitlines()
    assert rows[0].startswith("| ID | Severity") and rows[1].startswith("|---")
    assert "F-001" in rows[2] and "F-002" in rows[3], "high before medium"
    assert "\n\n|" not in findings, "a blank line inside a table breaks it into text"


def test_the_agents_own_headings_do_not_become_report_sections():
    from agents_orchestrator.code_review_agent.review_document import review_markdown

    md = review_markdown(_artifact())
    sections = [ln for ln in md.splitlines() if ln.startswith("## ")]
    assert sections == ["## Summary", "## Review checklist", "## Findings",
                        "## Requirements coverage", "## Design conformance", "## Scope and method"]
    # It may NAME the SBOM to say whose it is; it must not BE one.
    assert "## Software bill of materials" not in md and "### Direct dependencies" not in md
    assert "### Vulnerable dependencies" not in md and "### Hardcoded secrets" not in md
    assert "the SBOM and the security sign-off are the Security agent's report" in md
    assert "### Overview" in md


def test_a_whole_branch_states_its_coverage():
    from agents_orchestrator.code_review_agent.review_document import review_markdown

    md = review_markdown(_artifact())
    assert "read 11 of 14 reviewable files (325 lines of code; 20 files on the branch)" in md
    assert "**Not read:** tests/x.test.js" in md


def test_the_report_names_the_documents_the_code_was_checked_against():
    from agents_orchestrator.code_review_agent.review_document import review_markdown

    base = _artifact()["scope"]
    checked = review_markdown(_artifact(scope={**base, "documents": [
        {"title": "TEST_Project_BRD.docx", "stage": "requirements", "outcome": "ok"},
        {"title": "architecture.docx", "stage": "design", "outcome": "that document's text could not be extracted"},
    ]}))
    assert "**Checked against:** TEST_Project_BRD.docx (requirements); architecture.docx (design — could not be read: that document's text could not be extracted)" in checked
    assert "with the project's approved requirements and design documents" in checked

    none_on_file = review_markdown(_artifact(scope={**base, "documents": []}))
    assert "the project had no approved requirements or design document" in none_on_file

    # A review saved before documents were recorded claims neither.
    legacy = review_markdown(_artifact())
    assert "Checked against" not in legacy
    assert "approved requirements and design documents" not in legacy
    assert "had no approved requirements" not in legacy


def test_the_word_report_is_on_the_canvas_with_its_facts_and_a_page_copy(tmp_path):
    from docx import Document

    from agents_orchestrator.code_review_agent.review_document import write_review_report

    docx_path, md_path = write_review_report(_artifact(), str(tmp_path))
    assert os.path.basename(docx_path) == "QuickLink_Code_Review_feature_x_082f91e.docx"
    assert os.path.isfile(md_path) and open(md_path, encoding="utf-8").read().startswith("## Summary")
    band = "\n".join(c.text for t in Document(docx_path).tables for r in t.rows for c in r.cells)
    for text in ("CODE REVIEW REPORT", "Whole branch · feature/x", "QuickLink — Code review",
                 "Request changes", "11 of 14 files"):
        assert text in band, text
    # The facts are the REVIEW's: findings, and what it was checked against.
    assert "2 · 1 critical/high" in band
    assert "SBOM" not in band and "Vulnerabilities" not in band
    second, _ = write_review_report(_artifact(), str(tmp_path))
    assert second.endswith("_v2.docx"), "a second report never overwrites the first"


@pytest.mark.asyncio
async def test_the_saved_review_links_its_filed_report(tmp_path, monkeypatch):
    from agents_orchestrator.code_review_agent import code_review_agent_api as api
    from config.ws_helper import set_user_id

    set_user_id("user-1")
    monkeypatch.setattr("config.sdlcSettings", lambda: type("S", (), {"FILES": str(tmp_path)})())
    registered = AsyncMock(return_value="art-42")
    broadcast = AsyncMock()
    with patch("shared.services.chat_artifacts.register_generated_file", registered), \
         patch.object(api.manager, "broadcast", broadcast):
        doc = await api._write_review_document("sess-9", _artifact())

    assert doc["filename"].endswith(".docx") and "/generated/user-1/code_review/sess-9/output/" in doc["url"]
    args, kwargs = registered.await_args
    assert args[0] == doc["filename"] and kwargs["stage"] == "code_review"
    # The review points at ITS document row — every report for one commit shares a name.
    assert doc["artifact_id"] == "art-42"
    assert broadcast.await_args.args[0]["type"] == "file_generated"


@pytest.mark.asyncio
async def test_a_report_that_could_not_be_written_is_recorded_not_swallowed(monkeypatch):
    from agents_orchestrator.code_review_agent import code_review_agent_api as api

    def _boom(artifact, out_dir):
        raise OSError("disk full")

    monkeypatch.setattr("agents_orchestrator.code_review_agent.review_document.write_review_report", _boom)
    registered = AsyncMock()
    with patch("shared.services.chat_artifacts.register_generated_file", registered):
        doc = await api._write_review_document("sess-10", _artifact())
    assert doc == {"error": "The report document could not be written (OSError: disk full)"}
    registered.assert_not_awaited()


def test_the_report_is_a_standard_code_review_with_its_checklist():
    """Asked for: "a code review report of all the things a standard code review covers —
    has the security test been done, does it follow the architecture" — not a security report."""
    from agents_orchestrator.code_review_agent.review_document import review_checklist, review_markdown

    rows = {r["check"]: r for r in review_checklist(_artifact())}
    assert list(rows) == ["Whole change read", "Security issues in the code",
                          "Meets the approved requirements", "Follows the approved architecture",
                          "Logic and correctness", "Performance", "Maintainability and style", "Merge recommendation"]
    assert rows["Whole change read"]["result"] == "Partial"
    assert rows["Merge recommendation"]["result"] == "Request changes"

    # The security row is the REVIEWER's finding, not a scan: the fixture has one high
    # security finding, and no checklist row ever speaks for the scanners.
    assert rows["Security issues in the code"]["result"] == "Issues found"
    assert "1 critical/high security finding" in rows["Security issues in the code"]["detail"]
    clean = {r["check"]: r for r in review_checklist(_artifact(findings=[]))}
    assert clean["Security issues in the code"]["result"] == "None found"
    assert "the scan is the Security agent's" in clean["Security issues in the code"]["detail"]

    md = review_markdown(_artifact())
    assert "| Check | Result | Detail |" in md and "security report" not in md.lower()
