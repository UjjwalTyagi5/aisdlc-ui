"""Test-execution nodes (code + UI sub-agents).

Phase 4a — extracted from `super_agent.py`:
- run_code_testing_agent       (line 582)
- invoke_ui_testing_agent      (line 601)
- handle_ui_test_failure       (line 633)
- summarize_ui_test_results    (line 638)
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Optional

import aiofiles
from langchain_core.messages import AIMessage, HumanMessage

from agents_orchestrator.testing_agent.config.session_state import SuperAgentState
from agents_orchestrator.testing_agent.config.shared import (
    BROADCASTING_AVAILABLE,
    blog,
    get_llm,
    get_session_id,
    get_user_id,
    logger,
    record_response_usage,
)


# Sub-agents — Phase 4a moves them to canonical `tools/`. Legacy folder may still
# import them under the old paths until Phase 4b deletes the legacy mirror.
try:
    from agents_orchestrator.testing_agent.tools.code_testing_agent import app as code_testing_app
    from agents_orchestrator.testing_agent.tools.ui_testing_agent import run_agent as run_ui_testing_agent
except ImportError as exc:
    logger.error(
        f"Testing sub-agent unavailable (optional dependency missing) — testing agent disabled, "
        f"rest of the API will still load. Error: {exc}"
    )
    code_testing_app = None
    run_ui_testing_agent = None


async def run_code_testing_agent(state: SuperAgentState):
    logger.info("Invoking the Code Testing Sub-Agent...")
    blog("Running code testing sub-agent...")

    work_dir = state['work_dir']
    lang = state.get("language") or "python"

    generated_sets = state.get("generated_test_sets") or []
    skill_failures = [str(f) for f in (state.get("skill_failures") or [])]
    unit_sets = [
        s for s in generated_sets
        if isinstance(s, dict) and s.get("skill_name") == "unit" and s.get("test_file_path")
    ]
    unit_generation_failed = any(f.lower().startswith("unit:") or "skill:unit" in f.lower() for f in skill_failures)
    unit_file_missing = not any(os.path.isfile(s.get("test_file_path", "")) for s in unit_sets)
    if unit_generation_failed and unit_file_missing:
        failure_text = "\n".join(skill_failures) or "Unit test generation did not produce a runnable test file."
        summary = (
            "I did not run the repository's existing test suite because the requested "
            "generated unit test file was not produced.\n\n"
            "## Testing completed\n\n"
            "**Result:** ❌ Errored — generated unit tests were not runnable\n\n"
            "**Tests:** generated unit test file not produced\n\n"
            f"**Generation failure:**\n```\n{failure_text[:1000]}\n```"
        )
        blog("Generated unit tests missing; skipping existing-test-only execution", level="ERROR")
        return {
            "test_execution_summary": summary,
            "test_run_attempted": True,
            "test_runner_exit_code": 1,
            "test_runner_stdout": "",
            "test_runner_stderr": failure_text,
        }

    # Phase M.5 — Python keeps the existing LangGraph sub-agent (proven path
    # with pylint + LLM summarisation). .NET and React dispatch through their
    # runners directly (install + run_tests + parse) and emit a deterministic
    # summary built from the parsed XMLs.
    if lang == "python":
        requirements_path = os.path.join(work_dir, "requirements.txt")
        async with aiofiles.open(requirements_path, "w") as f:
            await f.write("pytest\npylint\npytest-cov\n")

        # Phase 8.10e — narrative hints threaded into the Python sub-agent
        # so summarize_node can render the same rich format the .NET / React
        # paths produce (lead-in paragraph + Test plan summary). Outer state
        # has these; inner GraphState would be blind to them otherwise.
        test_plan_obj = state.get("test_plan")
        plan_count_py = (
            len(test_plan_obj.test_cases)
            if test_plan_obj and test_plan_obj.test_cases else 0
        )
        plan_summary_py = None
        if test_plan_obj and test_plan_obj.test_cases:
            from collections import Counter
            types = Counter(
                (getattr(tc, "scenario_type", "") or "test").strip().lower()
                for tc in test_plan_obj.test_cases
            )
            plan_summary_py = ", ".join(f"{n} {t}" for t, n in types.most_common())
        code_analysis_py = state.get("code_analysis")
        fn_names_py = []
        if code_analysis_py and getattr(code_analysis_py, "functions", None):
            fn_names_py = [
                getattr(f, "function_name", "") for f in code_analysis_py.functions
            ]
            fn_names_py = [n for n in fn_names_py if n]
        input_filename_py = (
            os.path.basename(state.get("input_file_path") or "") or "your code"
        )

        loop = asyncio.get_running_loop()
        final_testing_state = await loop.run_in_executor(
            None,
            code_testing_app.invoke,
            {
                "project_path": work_dir,
                "input_filename": input_filename_py,
                "function_names": fn_names_py,
                "plan_test_case_count": plan_count_py,
                "plan_summary": plan_summary_py or "",
            },
        )
        summary = final_testing_state.get(
            "summary", "ERROR: Testing sub-agent did not produce a summary."
        )
        blog("Code testing completed (python)")

        # QoL — single-iteration coverage retry. If line coverage came in below
        # threshold (default 80) AND we haven't already iterated, generate a
        # supplementary test file targeting the uncovered lines and re-run pytest.
        # Caps at 1 iteration to bound LLM cost + wall time.
        try:
            iter_count = int(state.get("iter_count") or 0)
            threshold = float(state.get("coverage_threshold") or 80.0)
            if iter_count < 1:
                bumped_summary = await _maybe_iterate_coverage(state, work_dir, threshold, summary)
                if bumped_summary is not None:
                    return {"test_execution_summary": bumped_summary, "iter_count": iter_count + 1}
        except Exception as exc:
            logger.warning(f"coverage-iteration check failed (non-fatal): {exc}")

        return {"test_execution_summary": summary}

    # Non-Python: dispatch through the language runner.
    from agents_orchestrator.testing_agent.tools.runners import get_runner
    from agents_orchestrator.testing_agent.tools.sandbox.base import get_default_sandbox

    runner = get_runner(lang)
    sandbox = get_default_sandbox()
    loop = asyncio.get_running_loop()

    blog(f"Running {lang} runner: install → run_tests → parse")
    install_res = await loop.run_in_executor(None, runner.install, work_dir, sandbox)
    if not install_res.ok:
        blog(f"{lang} install failed: exit={install_res.exit_code}", level="ERROR")
    run_res = await loop.run_in_executor(None, runner.run_tests, work_dir, sandbox)
    cov = await loop.run_in_executor(None, runner.parse_coverage, work_dir)
    exec_ = await loop.run_in_executor(None, runner.parse_test_results, work_dir)
    lint = await loop.run_in_executor(None, runner.parse_lint, work_dir, sandbox)

    # Phase 7 — PR-scoped coverage. Only when (a) we cloned a PR branch and
    # (b) cobertura XML exists.
    pr_cov = None
    pr_branch = state.get("pr_branch")
    if pr_branch and cov and cov.coverage_xml_path:
        try:
            from agents_orchestrator.testing_agent.tools.pr_diff import (
                detect_default_branch, changed_line_ranges,
            )
            from agents_orchestrator.testing_agent.tools.artifact_builder import _compute_pr_coverage
            base = await loop.run_in_executor(None, detect_default_branch, work_dir)
            ranges = await loop.run_in_executor(None, changed_line_ranges, work_dir, base)
            if ranges:
                pr_cov = await loop.run_in_executor(
                    None, _compute_pr_coverage, cov.coverage_xml_path, ranges, base,
                )
        except Exception as exc:
            logger.warning(f"Phase 7: PR-coverage computation failed (skipping): {exc}")

    # Phase 8.6 — single source of truth for the chat-facing QA summary.
    # Replaces the old inline list-of-strings construction; same shape as
    # what the Python path now produces (was LLM-generated advisory text
    # like "Add a trailing newline at end of test_generated_by_agent.py").
    from agents_orchestrator.testing_agent.tools.qa_summary import build_qa_summary

    # Phase 8.10e — narrative + per-failure detail. All optional; degrades
    # gracefully when state pieces are missing.
    test_plan_obj = state.get("test_plan")
    plan_count = len(test_plan_obj.test_cases) if test_plan_obj and test_plan_obj.test_cases else 0

    plan_summary_text = None
    if test_plan_obj and test_plan_obj.test_cases:
        from collections import Counter
        types = Counter(
            (getattr(tc, "scenario_type", "") or "test").strip().lower()
            for tc in test_plan_obj.test_cases
        )
        plan_summary_text = ", ".join(
            f"{n} {t}" for t, n in types.most_common()
        )

    failures_list = []
    skipped_tests_list = []
    if exec_ and (exec_.failed or exec_.errors):
        from agents_orchestrator.testing_agent.tools.artifact_builder import _parse_test_failures
        junit_path = (
            (exec_.junit_xml_path if getattr(exec_, "junit_xml_path", None) else None)
            or os.path.join(work_dir, "reports", "results.xml")
        )
        try:
            failures_list = _parse_test_failures(junit_path)
        except Exception as exc:
            logger.warning(f"Phase 8.10e: _parse_test_failures failed: {exc}")
            failures_list = []
    if exec_ and getattr(exec_, "skipped", 0):
        from agents_orchestrator.testing_agent.tools.artifact_builder import _parse_skipped_tests
        junit_path = (
            (exec_.junit_xml_path if getattr(exec_, "junit_xml_path", None) else None)
            or os.path.join(work_dir, "reports", "results.xml")
        )
        try:
            skipped_tests_list = _parse_skipped_tests(junit_path, work_dir=work_dir)
        except Exception as exc:
            logger.warning(f"Phase 8.10e: _parse_skipped_tests failed: {exc}")
            skipped_tests_list = []

    # Phase 8.10g — evidence-bound narrative. Each clause only emits when
    # its corresponding piece of evidence actually exists, so the lead
    # paragraph never overstates what happened. Was a real bug pre-8.10g:
    # said "Ran X with coverage analysis" even when react-scripts was
    # missing and no execution / coverage actually occurred.
    input_file_basename = os.path.basename(state.get("input_file_path") or "")
    file_label = input_file_basename or "the cloned repo"
    code_analysis = state.get("code_analysis")
    has_analysis = bool(code_analysis and getattr(code_analysis, "functions", None))
    fn_names = (
        [getattr(f, "function_name", "") for f in code_analysis.functions[:3]]
        if has_analysis else []
    )
    fn_names = [n for n in fn_names if n]
    fn_str = ", ".join(f"`{n}()`" for n in fn_names)
    if fn_str and has_analysis and len(code_analysis.functions) > 3:
        fn_str += "…"
    runner_label = {"python": "pytest", "dotnet": "dotnet test", "react": "jest"}.get(lang, lang)

    parts = []
    # Subject — only claim "analyzed" when scan produced functions
    if has_analysis:
        parts.append(f"I analyzed `{file_label}`")
    elif input_file_basename:
        parts.append(f"I prepared tests for `{file_label}`")
    else:
        parts.append("I prepared tests")

    # Plan — only mention generated test cases when test_plan exists
    if plan_count and fn_str:
        parts.append(f", generated {plan_count} test case{'s' if plan_count != 1 else ''} covering {fn_str}")
    elif plan_count:
        parts.append(f", generated {plan_count} test case{'s' if plan_count != 1 else ''}")

    # Outcome — only claim "ran" / "with coverage" when evidence supports it
    if exec_ is not None and exec_.total > 0 and cov is not None:
        parts.append(f". Ran {runner_label} with coverage analysis. Here's what happened:")
    elif exec_ is not None and exec_.total > 0:
        parts.append(f". Ran {runner_label}. Test results were produced, but coverage was not collected.")
    elif exec_ is None and run_res is not None and run_res.exit_code != 0:
        parts.append(". I prepared the test run, but execution failed before results were produced.")
    else:
        parts.append(". Here's what happened:")

    narrative_intro = "".join(parts)

    # Phase 11.1 — at-a-glance dashboard inputs (test pyramid, coverage gaps,
    # skill failures). Skill failures + generated_test_sets come from
    # state populated by dispatch_test_types; coverage_files parsed from XML.
    generated_test_sets_for_summary = state.get("generated_test_sets") or []
    skill_failures_for_summary = state.get("skill_failures") or []
    coverage_files_for_summary: list = []
    if cov and getattr(cov, "coverage_xml_path", None):
        try:
            from agents_orchestrator.testing_agent.tools.coverage_html import (
                parse_per_file_coverage,
            )
            coverage_files_for_summary = parse_per_file_coverage(cov.coverage_xml_path) or []
        except Exception as exc:
            logger.warning(f"Phase 11.1: per-file coverage parse failed (non-fatal): {exc}")

    runner_output_for_summary = (
        (run_res.stderr or "").strip()
        or (run_res.stdout or "").strip()
        or f"Runner exited with code {run_res.exit_code}, but produced no stderr/stdout."
    )

    summary = build_qa_summary(
        lang=lang,
        runner_command=runner.runner_command,
        exec_=exec_,
        cov=cov,
        pr_cov=pr_cov,
        lint_exit=lint.exit_code,
        lint_tool=lint.tool,
        plan_test_case_count=plan_count,
        generated_test_file=None,  # the per-language runner save path; harmless to omit
        artifact_files=None,        # populated later in package_final_reports' summary
        untested_pr_files=(pr_cov.untested_files if pr_cov else None),
        run_res_exit=run_res.exit_code,
        run_res_stderr=runner_output_for_summary,
        # Phase 8.10e additions
        narrative_intro=narrative_intro,
        test_failures=failures_list or None,
        skipped_tests=skipped_tests_list or None,
        test_plan_summary=plan_summary_text,
        # Phase 11.1 — at-a-glance dashboard
        generated_test_sets=generated_test_sets_for_summary or None,
        skill_failures=skill_failures_for_summary or None,
        coverage_files=coverage_files_for_summary or None,
    )

    # Broadcast headline metrics via blog so the activity feed has them too.
    # Without this, if the orchestrator-broadcast `agent_response` event is
    # ever missed (WS reconnect during the long run, race with the broker's
    # session-bound dispatch), the user has NO visibility into test results
    # in the chat.
    if exec_:
        cov_part = f", cov={cov.coverage_pct}%" if cov else ""
        pr_part = f", PR-cov={pr_cov.coverage_pct}%" if pr_cov else ""
        blog(
            f"Test results: {exec_.passed}/{exec_.total} passed, "
            f"{exec_.failed} failed, {exec_.skipped} skipped "
            f"({exec_.duration_ms}ms){cov_part}{pr_part}"
        )
    blog(f"Code testing completed ({lang})")
    return {
        "test_execution_summary": summary,
        "pr_coverage": pr_cov.model_dump() if pr_cov else None,
        "test_run_attempted": True,
        "test_runner_exit_code": run_res.exit_code,
        "test_runner_stdout": run_res.stdout or "",
        "test_runner_stderr": run_res.stderr or "",
        "runner_command": runner.runner_command,
    }


async def invoke_ui_testing_agent(state: SuperAgentState):
    logger.info("Invoking the UI Testing Sub-Agent...")
    blog("Running UI testing sub-agent...")

    target_url = state['target_url']
    if not target_url:
        error_msg = "UI test intent routed, but no target URL was found in state."
        blog(error_msg, level="ERROR")
        return {"error_message": error_msg}

    try:
        import json as _json
        loop = asyncio.get_running_loop()
        session_id = get_session_id()
        user_id = get_user_id()

        # Parse the pre-approved test cases generated at approval time so the
        # Selenium agent executes exactly the cases the user reviewed.
        planned_cases = None
        raw_plan = state.get("ui_test_plan_json")
        if raw_plan:
            try:
                parsed = _json.loads(raw_plan)
                if isinstance(parsed, list) and parsed:
                    planned_cases = parsed
            except Exception:
                pass

        # BYOK (P3.6): run_ui_testing_agent (sync) re-enters asyncio in this fresh
        # executor thread; copy the current context so the run's resolved-model
        # contextvar crosses the thread boundary and _build_ui_llm() can read it.
        import contextvars as _contextvars
        _ctx = _contextvars.copy_context()
        results_data = await loop.run_in_executor(
            None,
            lambda: _ctx.run(
                run_ui_testing_agent,
                target_url, 12, session_id, user_id,
                state.get("user_prompt") or "",
                planned_cases,
            ),
        )

        if results_data is None:
            logger.warning("UI Testing agent returned None. This may indicate an early failure.")
            error_msg = (
                "The UI testing agent failed to run. This could be due to an invalid URL, "
                "a network issue, or a problem with the browser WebDriver."
            )
            blog(error_msg, level="ERROR")
            return {"error_message": error_msg}

        logger.info(f"UI Testing Sub-Agent finished, returned {len(results_data)} result items.")
        blog(f"UI testing completed with {len(results_data)} results")
        return {"ui_test_results": results_data, "error_message": None}
    except Exception as exc:
        logger.error(f"UI Testing Sub-Agent raised an exception: {exc}", exc_info=True)
        error_msg = f"The UI testing agent crashed with an error: {exc}. Please check the console logs."
        blog(error_msg, level="ERROR")
        return {"error_message": error_msg}


async def _maybe_iterate_coverage(
    state: SuperAgentState,
    work_dir: str,
    threshold: float,
    prior_summary: str,
) -> Optional[str]:
    """QoL — supplementary-test generation for uncovered Python lines.

    Returns a new summary if iteration ran, else None. Best-effort:
    parsing/regen failures fall back to the original summary (caller
    keeps it).
    """
    import xml.etree.ElementTree as ET
    from langchain_core.output_parsers import StrOutputParser
    from agents_orchestrator.testing_agent.Nodes.generate import _clean_llm_code_output

    cov_path = os.path.join(work_dir, "reports", "coverage.xml")
    if not os.path.exists(cov_path):
        return None

    # Pull line-level coverage gaps
    try:
        root = ET.parse(cov_path).getroot()
        line_rate = float(root.get("line-rate", "0") or 0) * 100.0
    except Exception:
        return None

    if line_rate >= threshold:
        return None

    # Walk packages → classes → lines, collect (file, line_number) pairs
    # for every <line number=N hits=0/>.
    uncovered: list = []
    for cls in root.iter("class"):
        filename = cls.get("filename") or ""
        if not filename or filename == "test_generated_by_agent.py":
            continue
        for ln in cls.iter("line"):
            try:
                if int(ln.get("hits", "0") or 0) == 0:
                    uncovered.append({"file": filename, "line": int(ln.get("number"))})
            except Exception:
                continue
    if not uncovered:
        return None

    logger.info(
        f"QoL: coverage {line_rate:.2f}% < {threshold}% threshold; "
        f"generating supplementary tests for {len(uncovered)} uncovered lines"
    )
    blog(f"Coverage at {line_rate:.2f}% — generating extra tests for {len(uncovered)} uncovered lines...")

    # Build a prompt that tells the LLM exactly which lines need coverage.
    # Use the same CRITICAL IMPORT RULES discipline as the primary prompt.
    sample = uncovered[:30]
    uncov_summary = "\n".join(f"  - {x['file']}:{x['line']}" for x in sample)
    prompt = (
        "You generated a pytest file but coverage is "
        f"{line_rate:.1f}% (below {threshold:.0f}% threshold). Below are uncovered\n"
        "lines that need additional tests. Generate a SECOND pytest file named\n"
        "`test_supplementary.py` containing ONLY new test functions for the\n"
        "uncovered code. Same rules as before: direct imports (no importlib /\n"
        "try-except / stubs / sys.path.append). Output raw Python only, starting\n"
        "with `import pytest`.\n\n"
        f"## Uncovered lines\n{uncov_summary}\n"
    )

    chain = get_llm() | StrOutputParser()
    loop = asyncio.get_running_loop()
    raw_code = await loop.run_in_executor(None, chain.invoke, prompt)
    record_response_usage(getattr(chain, "_last_response", None))  # best-effort
    cleaned = _clean_llm_code_output(raw_code)

    supp_path = os.path.join(work_dir, "test_supplementary.py")
    async with aiofiles.open(supp_path, "w", encoding="utf-8") as f:
        await f.write(cleaned)

    # Re-run pytest with the supplementary tests in place
    blog("Re-running pytest with supplementary tests...")
    final_testing_state = await loop.run_in_executor(
        None, code_testing_app.invoke, {"project_path": work_dir}
    )
    new_summary = final_testing_state.get("summary", prior_summary)

    bumped = (
        f"## Coverage iteration applied\n"
        f"- Initial line coverage: {line_rate:.2f}%\n"
        f"- Threshold: {threshold:.0f}%\n"
        f"- Generated supplementary tests for {len(sample)} uncovered lines\n"
        f"- Test file: test_supplementary.py\n\n"
        f"{new_summary}"
    )
    return bumped


async def handle_ui_test_failure(state: SuperAgentState):
    logger.error(f"UI Test Failure Handler: {state['error_message']}")
    blog(f"UI test failure: {state['error_message']}", level="ERROR")
    return {
        "final_user_message": state['error_message'],
        "ui_tests_completed": True,
        "pending_functional_approval": None,
        "functional_tests_approved": False,
    }


async def summarize_ui_test_results(state: SuperAgentState):
    logger.info("Summarizing UI test results...")
    blog("Summarizing UI test results...")

    results = state.get("ui_test_results")
    if not results:
        return {"final_user_message": "The UI testing agent ran but did not produce any test results."}

    passed_count = sum(1 for r in results if r['status'] == 'Pass')
    failed_count = len(results) - passed_count

    summary_prompt = f"""
You are a senior QA lead. Summarize the following UI test results in a concise, professional markdown format.
Highlight the number of passed and failed tests. If there are failures, briefly mention the descriptions of the failed test cases.
Test Results (JSON):
{json.dumps(results, indent=2)}
"""
    loop = asyncio.get_running_loop()
    response = await loop.run_in_executor(None, get_llm().invoke, summary_prompt)
    record_response_usage(response)
    summary = response.content

    history = state.get("chat_history", [])
    history.append(HumanMessage(content=state['user_prompt']))
    history.append(AIMessage(content=summary))

    blog(f"UI test summary generated - {passed_count} passed, {failed_count} failed")
    # Mark UI tests complete so decide_after_aggregate doesn't re-enter this path
    return {"final_user_message": summary, "chat_history": history, "ui_tests_completed": True}
