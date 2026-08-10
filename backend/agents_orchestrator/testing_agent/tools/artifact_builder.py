"""Cobertura/JUnit parsers + TestingArtifact assembler.

Phase 4a — extracted from `super_agent.py` lines 885-997 with no behavioural change.
Phase M will extend `_build_testing_artifact` to set `language` + `runner_command`,
and Phase B.2/B.3 to attach `pipeline_run` + `security_findings`.
"""
from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
from typing import Dict, List

from shared.models.testing import (
    CoverageSummary,
    DefectEntry,
    FunctionalScenarioResult,
    PipelineRun,
    SecurityFinding,
    TestCaseRef,
    TestExecution,
    TestingArtifact,
)

from agents_orchestrator.testing_agent.config.shared import logger


def _parse_coverage_xml(path: str) -> CoverageSummary:
    """Parse a Cobertura coverage.xml. Returns a populated CoverageSummary or
    a near-empty one if the file is malformed (we never raise into the agent flow)."""
    try:
        root = ET.parse(path).getroot()
        line_rate = float(root.get('line-rate', '0') or 0)
        branch_rate_attr = root.get('branch-rate')
        statements = int(root.get('lines-valid', '0') or 0)
        covered = int(root.get('lines-covered', '0') or 0)
        missed = max(statements - covered, 0)
        return CoverageSummary(
            statements=statements,
            missed=missed,
            coverage_pct=round(line_rate * 100.0, 2),
            branch_coverage_pct=round(float(branch_rate_attr) * 100.0, 2) if branch_rate_attr else None,
            coverage_xml_path=path,
        )
    except Exception as exc:
        logger.warning("_parse_coverage_xml failed (%s): %s", path, exc)
        return CoverageSummary(coverage_xml_path=path)


def _compute_pr_coverage(coverage_xml_path: str, changed_lines: dict, base_branch: str = "main"):
    """Phase 7 — intersect Cobertura per-line coverage with PR-changed line ranges.

    `changed_lines` is `{file_path: set(line_numbers)}` from
    `tools/pr_diff.changed_line_ranges`. Returns a `PRCoverageSummary` or
    `None` if there's nothing to compute.

    Cobertura XML structure:
      <coverage>
        <packages>
          <package>
            <classes>
              <class filename="path/relative/to/repo.py">
                <lines>
                  <line number="42" hits="3"/>
                  ...

    For each <class>, match `filename` against PR-changed file paths, then
    bucket each <line> as covered (hits>0) vs uncovered if its line number
    is in the PR's changed-line set for that file.
    """
    from shared.models.testing import PRCoverageSummary
    if not changed_lines:
        return None
    try:
        root = ET.parse(coverage_xml_path).getroot()
    except Exception as exc:
        logger.warning("_compute_pr_coverage: failed to parse %s: %s", coverage_xml_path, exc)
        return None

    # Build a normalized lookup: {basename: set(changed_lines)} too, since
    # Cobertura's filename may be relative to a different root than git's.
    changed_lookup_full: dict = {p: lines for p, lines in changed_lines.items()}
    changed_lookup_base: dict = {}
    for p, lines in changed_lines.items():
        base = p.rsplit("/", 1)[-1]
        changed_lookup_base.setdefault(base, set()).update(lines)

    total_changed = sum(len(s) for s in changed_lines.values())
    covered = 0
    files_with_any_coverage: set = set()

    for cls in root.iter("class"):
        fn = cls.get("filename") or ""
        # Try exact match, then suffix-match, then basename-match
        target = changed_lookup_full.get(fn)
        if target is None:
            for chf in changed_lookup_full:
                if fn.endswith(chf) or chf.endswith(fn):
                    target = changed_lookup_full[chf]
                    break
        if target is None:
            target = changed_lookup_base.get(fn.rsplit("/", 1)[-1])
        if not target:
            continue
        # Walk the class's <line> elements
        any_covered_in_file = False
        for line in cls.iter("line"):
            try:
                ln_num = int(line.get("number", "0") or 0)
                hits = int(line.get("hits", "0") or 0)
            except ValueError:
                continue
            if ln_num in target and hits > 0:
                covered += 1
                any_covered_in_file = True
        if any_covered_in_file:
            files_with_any_coverage.add(fn.rsplit("/", 1)[-1])

    untested = sorted(
        p for p in changed_lines
        if p.rsplit("/", 1)[-1] not in files_with_any_coverage and changed_lines[p]
    )
    pct = (covered / total_changed * 100.0) if total_changed else 0.0
    return PRCoverageSummary(
        base_branch=base_branch,
        files_changed=len([p for p, s in changed_lines.items() if s]),
        changed_lines_total=total_changed,
        changed_lines_covered=covered,
        coverage_pct=round(pct, 2),
        untested_files=untested[:10],  # cap at 10 to keep DB row reasonable
    )


def _parse_junit_xml(path: str) -> TestExecution:
    """Parse a JUnit-format pytest results.xml. Aggregates across all <testsuite> elements."""
    try:
        root = ET.parse(path).getroot()
        # Top-level <testsuites> wraps one or more <testsuite>; some tools omit the wrapper.
        suites = root.findall('testsuite') if root.tag == 'testsuites' else [root]
        total = passed = failed = skipped = errors = 0
        duration_s = 0.0
        for s in suites:
            tests = int(s.get('tests', '0') or 0)
            f = int(s.get('failures', '0') or 0)
            e = int(s.get('errors', '0') or 0)
            sk = int(s.get('skipped', '0') or 0)
            t = float(s.get('time', '0') or 0)
            total += tests
            failed += f
            errors += e
            skipped += sk
            duration_s += t
        passed = max(total - failed - errors - skipped, 0)
        return TestExecution(
            framework="pytest",
            total=total,
            passed=passed,
            failed=failed,
            skipped=skipped,
            errors=errors,
            duration_ms=int(duration_s * 1000),
            junit_xml_path=path,
        )
    except Exception as exc:
        logger.warning("_parse_junit_xml failed (%s): %s", path, exc)
        return TestExecution(framework="pytest", junit_xml_path=path)


def _parse_test_failures(junit_xml_path: str) -> List[Dict[str, str]]:
    """Phase 8.10e — extract per-test failure detail for the chat narrative.

    Returns: list of {"name": str, "type": "failure"|"error", "message": str}.
    Returns [] on missing/unparseable XML or no failures (best-effort — never
    raises). Used by the rich chat-response builder; existing artifact-status
    semantics are unchanged.
    """
    failures: List[Dict[str, str]] = []
    if not junit_xml_path or not os.path.isfile(junit_xml_path):
        return failures
    try:
        root = ET.parse(junit_xml_path).getroot()
    except Exception as exc:
        logger.warning("_parse_test_failures failed (%s): %s", junit_xml_path, exc)
        return failures
    for tc in root.iter("testcase"):
        name = tc.get("name") or "(unnamed)"
        for child in tc:
            tag = child.tag.lower()
            if tag in ("failure", "error"):
                msg = (child.get("message") or "").strip()
                body = (child.text or "").strip()
                # First non-empty line of stack/output — usually the assertion line
                first_line = next((ln for ln in body.splitlines() if ln.strip()), "")
                summary = (msg or first_line or "(no message)")[:200]
                failures.append({
                    "name": name,
                    "type": tag,
                    "message": summary,
                })
                break  # one failure entry per test case
    return failures


def _find_dotnet_skip_reason_in_source(work_dir: str | None, class_name: str, method_name: str) -> str:
    if not work_dir or not os.path.isdir(work_dir) or not method_name:
        return ""
    class_leaf = (class_name or "").split(".")[-1]
    method_re = re.compile(
        rf"(?P<attrs>(?:\s*\[[^\]]+\]\s*)+)\s*public\s+(?:async\s+)?(?:Task|void)\s+{re.escape(method_name)}\s*\(",
        re.MULTILINE,
    )
    skip_re = re.compile(r"""Skip\s*=\s*(?:"(?P<double>[^"]*)"|'(?P<single>[^']*)')""")
    for root, dirs, files in os.walk(work_dir):
        dirs[:] = [d for d in dirs if d.lower() not in {"bin", "obj", ".git"}]
        for filename in files:
            if not filename.endswith(".cs"):
                continue
            if class_leaf and class_leaf.lower() not in filename.lower():
                # Keep scanning generated chunk files, whose filename will not
                # necessarily match the class reported by the JUnit logger.
                if not filename.lower().startswith("generatedtests"):
                    continue
            path = os.path.join(root, filename)
            try:
                text = open(path, "r", encoding="utf-8").read()
            except Exception:
                continue
            match = method_re.search(text)
            if not match:
                continue
            skip_match = skip_re.search(match.group("attrs") or "")
            if skip_match:
                return (skip_match.group("double") or skip_match.group("single") or "").strip()
    return ""


def _parse_skipped_tests(junit_xml_path: str, work_dir: str | None = None) -> List[Dict[str, str]]:
    """Extract skipped test names and skip reasons from JUnit XML."""
    skipped: List[Dict[str, str]] = []
    if not junit_xml_path or not os.path.isfile(junit_xml_path):
        return skipped
    try:
        root = ET.parse(junit_xml_path).getroot()
    except Exception as exc:
        logger.warning("_parse_skipped_tests failed (%s): %s", junit_xml_path, exc)
        return skipped
    for tc in root.iter("testcase"):
        skipped_node = next((child for child in tc if child.tag.lower() == "skipped"), None)
        if skipped_node is None:
            continue
        name = tc.get("name") or "(unnamed)"
        classname = tc.get("classname") or ""
        message = (skipped_node.get("message") or skipped_node.text or "").strip()
        if not message:
            message = _find_dotnet_skip_reason_in_source(work_dir, classname, name)
        skipped.append({
            "name": name,
            "class": classname,
            "reason": message[:240] if message else "Runner did not emit a skip reason and no source-level skip annotation was found.",
        })
    return skipped


def _build_testing_artifact(state: dict, output_filenames: List[str]) -> TestingArtifact:
    """Assemble the TestingArtifact from final super_agent state + the list of files
    saved to the session output dir."""
    plan = state.get("test_plan")
    cases = []
    if plan and getattr(plan, "test_cases", None):
        for tc in plan.test_cases:
            cases.append(TestCaseRef(
                test_case_id=getattr(tc, "test_case_id", "") or "",
                feature_or_function_tested=getattr(tc, "feature_or_function_tested", "") or "",
                scenario_type=getattr(tc, "scenario_type", "") or "",
            ))

    coverage = None
    test_exec = None
    work_dir = state.get("work_dir")
    if work_dir:
        cov_path = os.path.join(work_dir, "reports", "coverage.xml")
        if os.path.exists(cov_path):
            coverage = _parse_coverage_xml(cov_path)
        junit_path = os.path.join(work_dir, "reports", "results.xml")
        if os.path.exists(junit_path):
            test_exec = _parse_junit_xml(junit_path)

    # Phase 8.10b — UI test path doesn't produce JUnit XML and doesn't go
    # through the test-plan generator. Status used to default to "failed"
    # (because test_exec was None and cases was empty) even when 100% of UI
    # tests passed. Inspect state['ui_test_results'] BEFORE the legacy code
    # path; if present, derive status from those results and synthesise a
    # TestExecution shim so downstream consumers (handoff payload,
    # deployment-agent context formatter) see consistent counts.
    ui_results = state.get("ui_test_results") or []
    if ui_results and not test_exec:
        ui_total = len(ui_results)
        ui_passed = sum(
            1 for r in ui_results
            if isinstance(r, dict) and str(r.get("status", "")).strip().lower() == "pass"
        )
        ui_failed = ui_total - ui_passed
        status = "executed_with_failures" if ui_failed else "executed"
        test_exec = TestExecution(
            framework="ui_browser",
            total=ui_total, passed=ui_passed, failed=ui_failed,
            errors=0, skipped=0, duration_ms=0,
            junit_xml_path="",
        )
    else:
        status = "plan_only"
        if test_exec:
            status = "executed_with_failures" if (test_exec.failed or test_exec.errors) else "executed"
        elif state.get("test_run_attempted"):
            status = "failed"
        if not cases:
            status = "failed" if status == "plan_only" else status

    # Defensive consistency: if unit-test generation failed and no generated
    # unit test set exists, do not report a clean executed artifact just
    # because a repository's pre-existing tests happened to run.
    skill_failures_raw = [str(f) for f in (state.get("skill_failures") or [])]
    generated_sets_raw = state.get("generated_test_sets") or []
    unit_generation_failed = any(f.lower().startswith("unit:") or "skill:unit" in f.lower() for f in skill_failures_raw)
    unit_generated = any(
        isinstance(s, dict) and s.get("skill_name") == "unit" and s.get("test_file_path")
        for s in generated_sets_raw
    )
    if unit_generation_failed and not unit_generated:
        status = "failed"

    # Phase B.2 bug-fix — pipeline-only runs (user said "run pipeline N", no
    # local pytest invoked). If a PipelineRun reached a terminal state, map
    # to pipeline_completed / pipeline_failed regardless of whether local
    # test cases were generated. This overrides the "failed" default above
    # so deployment doesn't see a misleading failed status.
    pipeline_dict = state.get("pipeline_run")
    if isinstance(pipeline_dict, dict) and pipeline_dict.get("state") == "completed":
        result = (pipeline_dict.get("result") or "").lower()
        if not test_exec and not cases:
            status = "pipeline_failed" if result in ("failed", "canceled") else "pipeline_completed"

    # Phase M.6 — language metadata
    lang = state.get("language") or "unknown"
    if lang not in ("python", "dotnet", "react"):
        lang = "unknown"

    # Phase B.2 — pipeline run record
    pipeline_run_dict = state.get("pipeline_run")
    pipeline_run = None
    if isinstance(pipeline_run_dict, dict):
        try:
            pipeline_run = PipelineRun(**pipeline_run_dict)
        except Exception as exc:
            logger.warning(f"Failed to coerce pipeline_run: {exc}")

    # Phase B.3 — security findings
    findings_raw = state.get("security_findings") or []
    findings: list = []
    if isinstance(findings_raw, list):
        for f in findings_raw:
            if isinstance(f, dict):
                try:
                    findings.append(SecurityFinding(**f))
                except Exception:
                    continue

    # Phase 7 — PR-scoped coverage (already computed in Nodes/execute.py and
    # stashed on state). Coerce dict back to model for type-safe artifact.
    from shared.models.testing import PRCoverageSummary
    pr_cov = None
    pr_cov_raw = state.get("pr_coverage")
    if isinstance(pr_cov_raw, dict):
        try:
            pr_cov = PRCoverageSummary(**pr_cov_raw)
        except Exception as exc:
            logger.warning(f"Failed to coerce pr_coverage: {exc}")

    # Phase 8.4 — rewrite XML path fields to durable output_dir copies. The
    # original `path` came from work_dir which gets deleted by
    # cleanup_workspace; the copies live in output_dir (created by
    # package_final_reports) and survive. Reading the artifact later (e.g.
    # from a re-validator agent) needs paths that actually resolve.
    output_dir_for_paths = state.get("output_dir") or ""
    if output_dir_for_paths:
        durable_cov = os.path.join(output_dir_for_paths, "coverage_report.xml")
        durable_junit = os.path.join(output_dir_for_paths, "results.xml")
        if coverage and os.path.exists(durable_cov):
            coverage.coverage_xml_path = durable_cov
        if test_exec and os.path.exists(durable_junit):
            test_exec.junit_xml_path = durable_junit

    return TestingArtifact(
        plan_test_case_count=len(cases),
        test_cases=cases,
        generated_test_code=state.get("generated_test_code"),
        test_execution=test_exec,
        coverage=coverage,
        pylint_report=None,                          # populated when sandboxing lands (Phase S)
        summary_md=state.get("final_user_message"),
        artifact_files=list(output_filenames),
        status=status,
        language=lang,                               # Phase M.6
        runner_command=state.get("runner_command"),  # Phase M.6
        pipeline_run=pipeline_run,                   # Phase B.2
        security_findings=findings,                  # Phase B.3
        pr_coverage=pr_cov,                          # Phase 7
        # Phase 10 — fan-out additive fields. All optional; deployment formatter
        # tolerates absence on legacy / standalone runs.
        functional_results=[
            FunctionalScenarioResult(**r) if isinstance(r, dict) else r
            for r in (state.get("aggregated_results") or {}).get("functional_results") or []
        ],
        defect_log=[
            DefectEntry(**d) if isinstance(d, dict) else d
            for d in (state.get("defect_log") or [])
        ],
        qa_report_html_path=state.get("qa_report_html_path"),
        qa_report_pdf_path=state.get("qa_report_pdf_path"),
    )
