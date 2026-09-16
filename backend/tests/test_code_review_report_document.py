"""The Code Review & Security Report: built from the review and the scan, never re-guessed.

What must hold in the document a reader acts on:
- a scanner that did not run is "Blocked" and its area "not established" — never clean;
- vulnerabilities are grouped per package with the version that fixes all of them and the
  direct dependency that brings the package in;
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
        "security": {
            "scanners": [
                {"name": "Gitleaks", "purpose": "Secrets", "status": "ok", "findings": 0, "seconds": 0.3, "message": ""},
                {"name": "Semgrep", "purpose": "Static analysis", "status": "error", "findings": None, "seconds": 1.0,
                 "message": "Semgrep exited with code 2"},
                {"name": "Trivy", "purpose": "Vulnerable dependencies", "status": "ok", "findings": 3, "seconds": 0.2, "message": ""},
            ],
            "secrets": [], "sast": [],
            "vulnerabilities": [
                {"id": "CVE-A", "severity": "critical", "package": "tar", "installed": "6.2.1", "fixed": "7.5.19", "title": "node-tar: tar: gzip bomb"},
                {"id": "CVE-B", "severity": "high", "package": "tar", "installed": "6.2.1", "fixed": "7.5.21", "title": "tar: traversal"},
                {"id": "CVE-C", "severity": "low", "package": "once", "installed": "1.1.2", "fixed": "3.0.1, 2.0.1", "title": "once: DoS"},
            ],
            "sbom": {
                "components": [
                    {"name": "sqlite3", "declared": "^5.1.7", "version": "5.1.7", "license": "BSD-3-Clause",
                     "scope": "runtime", "direct": True, "via": "", "vulnerabilities": 0},
                    {"name": "tar", "declared": "", "version": "6.2.1", "license": "ISC", "scope": "runtime",
                     "direct": False, "via": "sqlite3", "vulnerabilities": 2},
                ],
                "manifests": ["package.json"],
                "notes": ["package.json: no lockfile is committed — versions were resolved from the declared ranges at scan time"],
            },
            "totals": {"vulnerabilities": 3, "vulnerabilities_high": 2, "secrets": 0, "components": 2},
        },
        "security_summary": "Upgrade **sqlite3**.",
    }
    art.update(over)
    return art


def test_a_scanner_that_did_not_run_is_blocked_and_its_area_not_established():
    from agents_orchestrator.code_review_agent.review_document import review_markdown

    md = review_markdown(_artifact())
    assert "| Semgrep | Static analysis | Blocked | not run | Semgrep exited with code 2 |" in md
    assert "**Not established:** Semgrep did not run" in md
    assert "Static analysis did not run." in md
    assert "Static analysis found no OWASP Top 10 issues." not in md


def test_vulnerabilities_are_grouped_with_the_fix_for_all_and_where_they_come_from():
    from agents_orchestrator.code_review_agent.review_document import review_markdown

    md = review_markdown(_artifact())
    assert "| tar | 6.2.1 | Critical | 2 (1 critical, 1 high) | 7.5.21 | sqlite3 |" in md
    assert "| once | 1.1.2 | Low | 1 (1 low) | 3.0.1 | direct dependency |" in md
    assert "| CVE-A | Critical | tar 6.2.1 | gzip bomb | 7.5.19 |" in md, "the package prefix is stripped from titles"


def test_tables_are_single_blocks_and_findings_are_ordered_by_severity():
    from agents_orchestrator.code_review_agent.review_document import review_markdown

    md = review_markdown(_artifact())
    findings = md.split("## Findings", 1)[1].split("## Security review", 1)[0].strip()
    rows = findings.splitlines()
    assert rows[0].startswith("| ID | Severity") and rows[1].startswith("|---")
    assert "F-001" in rows[2] and "F-002" in rows[3], "high before medium"
    assert "\n\n|" not in findings, "a blank line inside a table breaks it into text"


def test_the_agents_own_headings_do_not_become_report_sections():
    from agents_orchestrator.code_review_agent.review_document import review_markdown

    md = review_markdown(_artifact())
    sections = [ln for ln in md.splitlines() if ln.startswith("## ")]
    assert sections == ["## Summary", "## Findings", "## Security review", "## Software bill of materials",
                        "## Requirements coverage", "## Design conformance", "## Scope and method"]
    assert "### Overview" in md


def test_a_whole_branch_states_its_coverage():
    from agents_orchestrator.code_review_agent.review_document import review_markdown

    md = review_markdown(_artifact())
    assert "read 11 of 14 reviewable files (325 lines of code; 20 files on the branch)" in md
    assert "**Not read:** tests/x.test.js" in md


def test_without_a_scan_nothing_about_security_is_claimed():
    from agents_orchestrator.code_review_agent.review_document import review_markdown

    md = review_markdown(_artifact(security={}))
    assert "The security review did not run for this target" in md
    assert "No known vulnerabilities" not in md and "The SBOM was not built." in md


def test_the_word_report_is_on_the_canvas_with_its_facts_and_a_page_copy(tmp_path):
    from docx import Document

    from agents_orchestrator.code_review_agent.review_document import write_review_report

    docx_path, md_path = write_review_report(_artifact(), str(tmp_path))
    assert os.path.basename(docx_path) == "QuickLink_Code_Review_feature_x_082f91e.docx"
    assert os.path.isfile(md_path) and open(md_path, encoding="utf-8").read().startswith("## Summary")
    band = "\n".join(c.text for t in Document(docx_path).tables for r in t.rows for c in r.cells)
    for text in ("CODE REVIEW & SECURITY REPORT", "Whole branch · feature/x", "QuickLink — Code review",
                 "Request changes", "3 · 2 high+", "11 of 14 files"):
        assert text in band, text
    second, _ = write_review_report(_artifact(), str(tmp_path))
    assert second.endswith("_v2.docx"), "a second report never overwrites the first"


@pytest.mark.asyncio
async def test_the_saved_review_links_its_filed_report(tmp_path, monkeypatch):
    from agents_orchestrator.code_review_agent import code_review_agent_api as api
    from config.ws_helper import set_user_id

    set_user_id("user-1")
    monkeypatch.setattr("config.sdlcSettings", lambda: type("S", (), {"FILES": str(tmp_path)})())
    registered = AsyncMock()
    broadcast = AsyncMock()
    with patch("shared.services.chat_artifacts.register_generated_file", registered), \
         patch.object(api.manager, "broadcast", broadcast):
        doc = await api._write_review_document("sess-9", _artifact())

    assert doc["filename"].endswith(".docx") and "/generated/user-1/code_review/sess-9/output/" in doc["url"]
    args, kwargs = registered.await_args
    assert args[0] == doc["filename"] and kwargs["stage"] == "code_review"
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
