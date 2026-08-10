"""Phase 8.6 — single source of truth for the chat-facing QA summary block.

Same shape for python / dotnet / react. No LLM in the summary path —
deterministic + reproducible + free of hallucinated lint advice.

Replaces both:
- The inline list-of-strings construction in Nodes/execute.py:run_code_testing_agent
  (.NET / React paths)
- The LLM-summarised summary in tools/code_testing_agent.py:summarize_node
  (Python path) — which was generating ad hoc advisory bullets like
  "Add a trailing newline at the end of test_generated_by_agent.py" because
  Claude got raw pylint stdout + cobertura XML and a vague "summarise" prompt.

Phase 8.9b — switch from line-joined to block-joined output. Single `\\n`
between adjacent non-blank markdown lines collapses to a SPACE in the
chat renderer (CommonMark), so users saw all the **Result/Tests/Coverage**
lines run together as one paragraph. Each metric is now emitted as a
standalone paragraph (joined with `\\n\\n`); multi-line groups (the bullet
list, the code-fenced stderr block) are kept internally `\\n`-joined so
they render as a single list / fence.
"""
from __future__ import annotations

from typing import Dict, List, Optional


def _is_application_source_coverage_file(item: Dict) -> bool:
    bucket = str(item.get("bucket") or "")
    filename = str(item.get("filename") or "").replace("\\", "/").lower()
    if bucket:
        return bucket == "Application source"
    return not (
        "/views/" in f"/{filename}"
        or filename.endswith(".cshtml")
        or filename.endswith(".g.cs")
        or "/obj/" in f"/{filename}"
        or "/bin/" in f"/{filename}"
        or "/migrations/" in f"/{filename}"
        or filename.endswith("modelsnapshot.cs")
        or filename.endswith(".designer.cs")
        or filename.endswith("program.cs")
        or filename.endswith("startup.cs")
    )


def _application_source_coverage(coverage_files: Optional[List[Dict]]) -> Optional[Dict[str, float]]:
    source_files = [
        item for item in (coverage_files or [])
        if item.get("filename") and _is_application_source_coverage_file(item)
    ]
    statements = sum(int(item.get("statements") or 0) for item in source_files)
    covered = sum(int(item.get("covered") or 0) for item in source_files)
    if statements <= 0:
        return None
    return {
        "coverage_pct": covered / statements * 100.0,
        "statements": statements,
        "covered": covered,
        "missed": max(statements - covered, 0),
        "files": len(source_files),
    }


def build_qa_summary(
    *,
    lang: str,                                          # "python" | "dotnet" | "react"
    runner_command: str,
    exec_=None,                                          # TestExecution or None
    cov=None,                                            # CoverageSummary or None
    pr_cov=None,                                         # PRCoverageSummary or None
    lint_exit: Optional[int] = None,
    lint_tool: str = "lint",
    plan_test_case_count: int = 0,
    generated_test_file: Optional[str] = None,
    artifact_files: Optional[List[str]] = None,
    untested_pr_files: Optional[List[str]] = None,
    run_res_exit: Optional[int] = None,
    run_res_stderr: str = "",
    # Phase 8.10e — narrative + per-failure detail. All optional; missing →
    # output degrades gracefully to the existing terse metric block.
    narrative_intro: Optional[str] = None,              # one-paragraph lead-in
    test_failures: Optional[List[Dict[str, str]]] = None,  # [{name, type, message}, …]
    skipped_tests: Optional[List[Dict[str, str]]] = None,  # [{name, class, reason}, ...]
    test_plan_summary: Optional[str] = None,            # e.g. "5 happy path, 3 edge case"
    # Phase 11.1 — at-a-glance QA dashboard fields. All optional; render
    # only when populated. Goal: lift the QA report's most-actionable info
    # into the chat reply so the user sees defects + coverage gaps + test-
    # type breakdown at-a-glance, without relying on the QA report buttons.
    generated_test_sets: Optional[List[Dict]] = None,   # [{skill_name, test_framework, scenario_count}, …]
    skill_failures: Optional[List[str]] = None,         # ["functional_api: TimeoutError: …", …]
    coverage_files: Optional[List[Dict]] = None,        # [{filename, coverage_pct, statements, missed}, …]
) -> str:
    """Build a polished QA-style chat summary block.

    Output shape (Markdown):

        ## Testing completed

        **Result:** ✅ Passed

        **Tests:** 67 total — 67 passed, 0 failed, 0 errors, 0 skipped (1827ms)

        **Coverage:** 100.0% line (24 stmts, 0 missed)

        **PR Coverage:** 87% changed lines covered (15/17 across 3 files)

        **Generated test cases:** 28

        **Generated test file:** `GeneratedTests.cs`

        **Runner:** `dotnet test ...`

        **Artifacts:** test_plan.xlsx, coverage_report.xml, results.xml

        **Untested PR files:**
        - `DuplicateCheckResult.cs`

    Each scalar metric is its own paragraph (joined with blank lines)
    so the chat renderer doesn't collapse them into one wall of text.
    The bullet list and stderr code block are kept as cohesive blocks.
    """
    blocks: List[str] = []

    # Phase 8.10e — narrative lead-in: makes the testing agent feel like
    # dev/ingestion/design (which always narrate before showing results).
    # Skip cleanly if absent.
    if narrative_intro:
        blocks.append(narrative_intro.strip())

    blocks.append("## Testing completed")

    # Result line — most important; always rendered
    if exec_ is None:
        if run_res_exit is not None and run_res_exit != 0:
            blocks.append("**Result:** ❌ Errored — test runner failed before producing results")
        else:
            blocks.append("**Result:** ⚠️ No test results produced")
    else:
        if exec_.errors:
            result = "❌ Errored"
        elif exec_.failed:
            result = f"⚠️ Failed ({exec_.failed} of {exec_.total} test{'s' if exec_.total != 1 else ''} failed)"
        elif exec_.total == 0:
            result = "⚠️ No tests collected"
        else:
            result = "✅ Passed"
        blocks.append(f"**Result:** {result}")

    # Tests
    if exec_ is not None:
        blocks.append(
            f"**Tests:** {exec_.total} total — {exec_.passed} passed, "
            f"{exec_.failed} failed, {exec_.errors} errors, {exec_.skipped} skipped "
            f"({exec_.duration_ms}ms)"
        )
    else:
        blocks.append("**Tests:** results.xml not produced")

    # Phase 8.10e — Test plan summary (e.g. "5 happy path, 3 edge case")
    # gives the user a quick read on what kinds of scenarios were covered
    # without forcing them to open the Excel.
    if test_plan_summary:
        blocks.append(f"**Test plan:** {test_plan_summary}")

    # Coverage. Prefer the client-facing application-source metric when
    # per-file coverage is available; keep raw Cobertura overall as context
    # because it includes views/startup/generated files.
    if cov is not None:
        app_cov = _application_source_coverage(coverage_files)
        if app_cov:
            blocks.append(
                f"**Application source coverage:** {app_cov['coverage_pct']:.1f}% "
                f"({int(app_cov['covered'])}/{int(app_cov['statements'])} stmts covered, "
                f"{int(app_cov['missed'])} missed across {int(app_cov['files'])} files)"
            )
            blocks.append(
                f"**Overall coverage:** {cov.coverage_pct}% line ({cov.statements} stmts, {cov.missed} missed; "
                "includes views/startup/generated files)"
            )
        else:
            blocks.append(
                f"**Coverage:** {cov.coverage_pct}% line ({cov.statements} stmts, {cov.missed} missed)"
            )

    # PR-scoped coverage (Phase 7) — only when applicable
    if pr_cov is not None:
        blocks.append(
            f"**PR Coverage:** {pr_cov.coverage_pct}% changed lines covered "
            f"({pr_cov.changed_lines_covered}/{pr_cov.changed_lines_total} across {pr_cov.files_changed} file"
            f"{'s' if pr_cov.files_changed != 1 else ''}, vs {pr_cov.base_branch})"
        )

    # Generated tests info (Phase 7 + plan)
    if plan_test_case_count:
        blocks.append(f"**Generated test cases:** {plan_test_case_count}")
    if generated_test_file:
        blocks.append(f"**Generated test file:** `{generated_test_file}`")

    # Runner command
    if runner_command:
        blocks.append(f"**Runner:** `{runner_command}`")

    # Lint
    if lint_exit is not None:
        ok = " ✅" if lint_exit == 0 else " ⚠️"
        blocks.append(f"**Lint ({lint_tool}):** exit={lint_exit}{ok}")

    # Artifact files
    if artifact_files:
        files_str = ", ".join(artifact_files[:8])
        if len(artifact_files) > 8:
            files_str += f" (+{len(artifact_files)-8} more)"
        blocks.append(f"**Artifacts:** {files_str}")

    # Untested PR files — multi-line bullet list kept as one block so
    # markdown renders it as a single contiguous list (consecutive `- x`
    # lines without blank-line separators).
    if untested_pr_files:
        bullet_lines = ["**Untested PR files:**"]
        for f in untested_pr_files[:5]:
            bullet_lines.append(f"- `{f}`")
        if len(untested_pr_files) > 5:
            bullet_lines.append(f"- (+{len(untested_pr_files)-5} more)")
        blocks.append("\n".join(bullet_lines))

    # Phase 11.1 — Test pyramid: per-skill counts in a compact markdown table
    # so users see at-a-glance which test types ran (vs. the terse single
    # "Tests: N total" line). Renders only when the fan-out actually produced
    # results — falls back cleanly for legacy single-test-code runs.
    if generated_test_sets:
        pyramid_lines = ["**Test pyramid:**"]
        pyramid_lines.append("| Type | Framework | Scenarios |")
        pyramid_lines.append("|---|---|---|")
        # Stable display order matching the test-pyramid hierarchy
        order = ("unit", "negative_edge", "integration", "contract", "smoke",
                 "functional_api", "functional_ui", "accessibility",
                 "property_based", "mutation_testing", "security_static",
                 "dependency_scan")
        by_name = {s.get("skill_name"): s for s in generated_test_sets if s.get("skill_name")}
        rendered_any = False
        for name in order:
            if name in by_name:
                s = by_name[name]
                pyramid_lines.append(
                    f"| {name.replace('_', ' ').title()} | "
                    f"{s.get('test_framework', '-')} | "
                    f"{s.get('scenario_count', 0)} |"
                )
                rendered_any = True
        # Append any unrecognised skill names at the end
        for s in generated_test_sets:
            name = s.get("skill_name")
            if name and name not in order:
                pyramid_lines.append(
                    f"| {name.replace('_', ' ').title()} | "
                    f"{s.get('test_framework', '-')} | "
                    f"{s.get('scenario_count', 0)} |"
                )
                rendered_any = True
        if rendered_any:
            blocks.append("\n".join(pyramid_lines))

    # Phase 11.1 — Top uncovered files (top 3) so users see WHERE the
    # coverage gaps are, not just an aggregate %. Renders only when
    # coverage_files has data; legacy runs without per-file coverage skip.
    if coverage_files:
        worst = sorted(
            (
                f for f in coverage_files
                if f.get("filename")
                and _is_application_source_coverage_file(f)
                and int(f.get("missed") or 0) > 0
            ),
            key=lambda f: (f.get("coverage_pct", 100.0), -int(f.get("missed") or 0)),
        )[:3]
        if worst:
            cov_lines = ["**Top uncovered application files:**"]
            for f in worst:
                pct = f.get("coverage_pct", 0.0)
                stmts = f.get("statements", "?")
                missed = f.get("missed", "?")
                cov_lines.append(
                    f"- `{f['filename']}` — **{pct:.1f}%** ({missed}/{stmts} stmts uncovered)"
                )
            blocks.append("\n".join(cov_lines))

    # Phase 11.1 — Skill failures (test types whose generation crashed).
    # Distinct from test_failures (assertion failures within a passing skill);
    # this block flags entire skills that didn't even produce a runnable file.
    if skill_failures:
        sf_lines = ["**Skill generation failures:**"]
        for f in skill_failures[:5]:
            sf_lines.append(f"- {f}")
        if len(skill_failures) > 5:
            sf_lines.append(f"- (+{len(skill_failures)-5} more)")
        blocks.append("\n".join(sf_lines))

    # Phase 8.10e — Failures block. Per-test name + first 1-2 lines of
    # error/assertion message so the user can see WHICH test failed and
    # WHY without opening the zip. Cap at 8 to stay readable; overflow line
    # tells the user where to look for the rest.
    if test_failures:
        fail_lines = ["**Failures:**"]
        for f in test_failures[:8]:
            name = f.get("name", "(unnamed)")
            ftype = f.get("type", "failure")
            message = f.get("message", "(no message)")
            fail_lines.append(f"- `{name}` ({ftype}): {message}")
        if len(test_failures) > 8:
            fail_lines.append(f"- (+{len(test_failures)-8} more — see results.xml)")
        blocks.append("\n".join(fail_lines))

    if skipped_tests:
        skip_lines = [
            "**Skipped tests:**",
            "These tests were discovered by the runner but intentionally not executed. Review the reason before sharing externally.",
        ]
        for item in skipped_tests[:8]:
            name = item.get("name", "(unnamed)")
            reason = item.get("reason") or "Runner did not emit a skip reason and no source-level skip annotation was found."
            klass = item.get("class")
            label = f"{klass}.{name}" if klass else name
            skip_lines.append(f"- `{label}`: {reason}")
        if len(skipped_tests) > 8:
            skip_lines.append(f"- (+{len(skipped_tests)-8} more - see results.xml)")
        blocks.append("\n".join(skip_lines))
    elif exec_ is not None and getattr(exec_, "skipped", 0):
        blocks.append(
            "**Skipped tests:**\n"
            f"- {exec_.skipped} test(s) were reported as skipped, but the test-results XML did not include per-test skip reasons. "
            "Open results.xml or the native runner log for the authoritative skip details before sharing externally."
        )

    # Runner stderr — multi-line code block kept internally `\n`-joined.
    if run_res_exit is not None and run_res_exit != 0 and (exec_ is None or not exec_.failed):
        stderr_text = (run_res_stderr or "")[:300]
        blocks.append(
            "**Runner stderr (truncated):**\n```\n" + stderr_text + "\n```"
        )

    # Phase 8.9b — `\n\n` between blocks so each metric becomes its own
    # paragraph in the chat renderer (CommonMark collapses single `\n` into
    # a space within a paragraph).
    return "\n\n".join(blocks)
