"""The test run report — the numbers a run produced, shaped for the page.

WHY A STRUCTURED REPORT. The Testing page showed the agent's prose reply as raw
text — `##` and `**` on screen, one long bubble — with the answer to "did it pass,
and how well is it covered" buried in paragraph six. The run had already written
every number to disk: `testing_artifact.json` (execution, coverage, cases, defects),
`results.xml` (each test), `coverage_report.xml` (each file). This reads those and
hands the page one JSON it can render as a report: a verdict, key facts, per-test
rows, per-file coverage bars, the generated cases, the defects.

WHAT COUNTS AS A DEFECT HERE, beyond the artifact's own defect log: a generated test
suite that jest could not even parse. The run above reported "Passed 2/2" while its
own `generated_unit.test.jsx` died on "Jest encountered an unexpected token" — the
two passing tests were the repo's. A suite that never ran is a failure of the run,
and the report says so in red rather than leaving it in a stderr dump.

Pure reads; never raises for a missing piece — a report with fewer sections beats a
500 on a page that has a result to show.
"""
from __future__ import annotations

import json
import logging
import os
import re
import xml.etree.ElementTree as ET
from typing import Any, Optional

logger = logging.getLogger("testing_agent.run_report")

_SUITE_FAILED_RE = re.compile(r"FAIL\s+(\S+)\s*\n\s*●\s*Test suite failed to run\s*\n+\s*(.+)", re.MULTILINE)


def _read_json(path: str) -> Optional[dict]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:  # noqa: BLE001
        return None


def _junit_tests(path: str) -> list[dict]:
    """Every testcase in a JUnit XML: name, suite, status, duration, message."""
    if not os.path.isfile(path):
        return []
    try:
        root = ET.parse(path).getroot()
    except Exception:  # noqa: BLE001
        return []
    rows: list[dict] = []
    for tc in root.iter("testcase"):
        status, message = "passed", ""
        for child in tc:
            tag = child.tag.split("}")[-1]
            if tag in ("failure", "error"):
                status = "failed" if tag == "failure" else "error"
                message = (child.get("message") or (child.text or "").strip())[:400]
            elif tag == "skipped":
                status = "skipped"
                message = (child.get("message") or "")[:200]
        try:
            duration_ms = int(round(float(tc.get("time") or 0) * 1000))
        except ValueError:
            duration_ms = 0
        rows.append({
            "name": tc.get("name") or "",
            "suite": tc.get("classname") or "",
            "status": status,
            "durationMs": duration_ms,
            "message": message,
        })
    return rows


def _coverage_files(path: str) -> list[dict]:
    if not os.path.isfile(path):
        return []
    try:
        from agents_orchestrator.testing_agent.tools.coverage_html import parse_per_file_coverage  # noqa: PLC0415

        rows = parse_per_file_coverage(path) or []
    except Exception:  # noqa: BLE001
        return []
    out = []
    for r in rows:
        out.append({
            "path": str(r.get("filename") or "").replace("\\", "/"),
            "pct": float(r.get("coverage_pct") or 0),
            "statements": int(r.get("statements") or 0),
            "covered": int(r.get("covered") or 0),
            "missed": int(r.get("missed") or 0),
            "bucket": str(r.get("bucket") or ""),
        })
    out.sort(key=lambda r: (r["pct"], -r["statements"]))
    return out


def unrunnable_suites(stderr: str, generated_files: list[str]) -> list[dict]:
    """Generated suites the runner could not start, from jest's stderr."""
    if not stderr:
        return []
    names = {os.path.basename(f).replace("\\", "/") for f in generated_files if f}
    found: list[dict] = []
    for m in _SUITE_FAILED_RE.finditer(stderr):
        path = m.group(1).replace("\\", "/")
        if names and os.path.basename(path) not in names:
            continue
        found.append({"file": path, "reason": " ".join(m.group(2).split())[:300]})
    return found


def _relative_to_workspace(path: str, work_dir: Any) -> str:
    if work_dir and path.startswith(str(work_dir)):
        return os.path.relpath(path, str(work_dir)).replace("\\", "/")
    return os.path.basename(path)


REPORT_FILENAME = "run_report.json"


def write_run_report(output_dir: str, *, session_id: str, state: Optional[dict]) -> Optional[dict]:
    """Build the report with the run's in-memory state (stderr, generated files, the
    clone target) and persist it beside the run's other files, so the page can read
    it after the process — and its SESSION_STATES — has gone."""
    report = build_run_report(output_dir, session_id=session_id, state=state)
    if report is None:
        return None
    try:
        with open(os.path.join(output_dir, REPORT_FILENAME), "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=1)
    except OSError as exc:
        logger.warning("run report not written: %s", exc)
    return report


def read_run_report(output_dir: str, *, session_id: str, state: Optional[dict] = None) -> Optional[dict]:
    """The persisted report when there is one, else one built from the files."""
    saved = _read_json(os.path.join(output_dir, REPORT_FILENAME))
    if saved:
        return saved
    return build_run_report(output_dir, session_id=session_id, state=state)


def build_run_report(output_dir: str, *, session_id: str, state: Optional[dict] = None) -> Optional[dict]:
    """The report for one session, or None when the session produced nothing."""
    artifact = _read_json(os.path.join(output_dir, "testing_artifact.json"))
    if not artifact and not os.path.isdir(output_dir):
        return None
    artifact = artifact or {}
    state = state or {}

    execution = artifact.get("test_execution") or {}
    coverage = artifact.get("coverage") or {}
    language = artifact.get("language") or state.get("language") or ""
    framework = {"python": "pytest", "dotnet": "xunit", "react": "jest"}.get(language, execution.get("framework") or "")

    tests = _junit_tests(os.path.join(output_dir, "results.xml"))
    files = _coverage_files(os.path.join(output_dir, "coverage_report.xml"))

    generated = [
        str(s.get("test_file_path") or "") for s in (state.get("generated_test_sets") or []) if isinstance(s, dict)
    ]
    broken = unrunnable_suites(state.get("test_runner_stderr") or "", generated)

    defects: list[dict] = []
    for d in artifact.get("defect_log") or []:
        if isinstance(d, dict):
            defects.append({
                "id": d.get("defect_id") or "",
                "severity": (d.get("severity") or "medium").lower(),
                "summary": d.get("summary") or "",
                "detail": d.get("stack_trace") or d.get("reproducer") or "",
            })
    for i, b in enumerate(broken, start=1):
        defects.append({
            "id": f"DEF-SUITE-{i:03d}",
            "severity": "high",
            "summary": f"Generated test suite could not run: {os.path.basename(b['file'])}",
            "detail": b["reason"],
        })

    total = int(execution.get("total") or 0)
    failed = int(execution.get("failed") or 0) + int(execution.get("errors") or 0)
    if artifact.get("status") in ("failed", "error") and total == 0:
        verdict = "error"
    elif total == 0:
        verdict = "no_tests"
    elif failed or broken:
        verdict = "failed" if failed else "partial"
    else:
        verdict = "passed"

    cases = []
    for c in artifact.get("test_cases") or []:
        if isinstance(c, dict):
            cases.append({
                "id": c.get("test_case_id") or "",
                "feature": c.get("feature_or_function_tested") or "",
                "summary": c.get("test_summary") or "",
                "scenarioType": c.get("scenario_type") or "",
                "steps": c.get("test_steps") or "",
                "data": c.get("test_data") or "",
                "expected": c.get("expected_result") or "",
            })

    clone = state.get("clone_target") or {}
    threshold = None
    try:
        threshold = float((state.get("test_config") or {}).get("coverage_threshold") or 0) or None
    except (TypeError, ValueError):
        threshold = None

    app_files = [f for f in files if f["bucket"] == "Application source"] or files
    app_stmts = sum(f["statements"] for f in app_files)
    app_cov = sum(f["covered"] for f in app_files)

    return {
        "sessionId": session_id,
        "verdict": verdict,
        "testTypes": [str(t) for t in (state.get("selected_test_types") or []) if t] or (["unit"] if tests else []),
        "language": language,
        "framework": framework,
        "runnerCommand": artifact.get("runner_command") or state.get("runner_command") or "",
        "target": {
            "project": str(clone.get("project") or ""),
            "repo": str(clone.get("repo") or ""),
            "branch": str(clone.get("branch") or ""),
        },
        "execution": {
            "total": total,
            "passed": int(execution.get("passed") or 0),
            "failed": int(execution.get("failed") or 0),
            "skipped": int(execution.get("skipped") or 0),
            "errors": int(execution.get("errors") or 0),
            "durationMs": int(execution.get("duration_ms") or 0),
        },
        "coverage": {
            "linePct": float(coverage.get("coverage_pct") or 0),
            "branchPct": float(coverage.get("branch_coverage_pct") or 0) if coverage.get("branch_coverage_pct") is not None else None,
            "statements": int(coverage.get("statements") or 0),
            "missed": int(coverage.get("missed") or 0),
            "applicationPct": round(100.0 * app_cov / app_stmts, 1) if app_stmts else None,
            "thresholdPct": threshold,
            "files": files,
        },
        "tests": tests,
        "testCases": cases,
        "defects": defects,
        "unrunnableSuites": broken,
        "generatedFiles": [_relative_to_workspace(f, state.get("work_dir")) for f in generated],
        "artifactFiles": [str(f) for f in (artifact.get("artifact_files") or [])],
        "qaReportAvailable": os.path.isfile(os.path.join(output_dir, "qa_report.html")),
        "lintExit": state.get("lint_exit_code"),
        "summaryMd": artifact.get("summary_md") or "",
    }
