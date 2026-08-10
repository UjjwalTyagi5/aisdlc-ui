"""Code + requirement analysis nodes.

Phase 4a — extracted from `super_agent.py` IntentDrivenAgent methods at:
- analyze_requirements      (line 307)
- find_and_analyze_code     (line 383)
- _scan_python_files        (line 433)
- handle_analysis_failure   (line 459)

Phase 1.5 fix preserved: AST-derived `file_path` is overwritten on the LLM result
to defend against the model emitting `<UNKNOWN>`. Phase 1.6 fix preserved:
per-function analysis is parallelised via `asyncio.gather` (was sequential w/ 10s sleep).
"""
from __future__ import annotations

import ast
import asyncio
import os
from typing import Dict, List

from agents_orchestrator.testing_agent.config.session_state import (
    CodeAnalysis,
    FunctionInfo,
    RequirementAnalysis,
    RequirementInfo,
    SuperAgentState,
)
from agents_orchestrator.testing_agent.config.shared import blog, get_llm, logger
from agents_orchestrator.testing_agent.tools.runners import get_runner


async def analyze_requirements(state: SuperAgentState):
    logger.info("Analyzing requirements from text input...")
    blog("Analyzing requirements from text input...")

    # Phase 5 — short-circuit when upstream req agent already produced stories.
    # Hydrate RequirementAnalysis directly; skip the LLM call entirely.
    upstream = state.get("upstream_requirements")
    if isinstance(upstream, dict) and upstream.get("stories"):
        stories = upstream["stories"]
        hydrated = []
        for i, s in enumerate(stories):
            req_id = s.get("id") or s.get("requirement_id") or f"REQ-{i+1:02d}"
            description = s.get("title") or s.get("description") or ""
            ac_raw = s.get("acceptance_criteria") or []
            if isinstance(ac_raw, str):
                ac_list = [line.strip("-* \t") for line in ac_raw.split("\n") if line.strip()]
            elif isinstance(ac_raw, list):
                ac_list = [
                    (item.get("description") if isinstance(item, dict) else str(item))
                    for item in ac_raw
                ]
                ac_list = [a for a in ac_list if a]
            else:
                ac_list = []
            if not ac_list:
                ac_list = [""]  # pydantic requires List[str], even if empty content
            hydrated.append(RequirementInfo(
                requirement_id=str(req_id),
                description=description,
                acceptance_criteria=ac_list,
            ))
        analysis = RequirementAnalysis(requirements=hydrated)
        logger.info(f"Phase 5: hydrated {len(hydrated)} requirements from upstream payload (no LLM call)")
        blog(f"Hydrated {len(hydrated)} requirements from upstream payload (no LLM call)")
        return {"requirement_analysis": analysis}

    content = state.get("input_content")
    if not content or not content.strip():
        logger.warning("No content to analyze for requirements.")
        blog("No content available for requirement analysis", level="WARNING")
        return {"requirement_analysis": RequirementAnalysis(requirements=[])}

    prompt = f"You are a business analyst. Read this text and extract key functional requirements: {content}"
    analyzer_llm = get_llm().with_structured_output(RequirementAnalysis, method="function_calling")

    try:
        loop = asyncio.get_running_loop()
        analysis = await loop.run_in_executor(None, analyzer_llm.invoke, prompt)
        logger.info(f"Analysis complete. Found {len(analysis.requirements)} requirements.")
        blog(f"Found {len(analysis.requirements)} requirements")
        return {"requirement_analysis": analysis}
    except Exception as exc:
        logger.error(f"ERROR during requirement analysis: {exc}")
        blog(f"Requirement analysis failed: {exc}", level="ERROR")
        return {"requirement_analysis": RequirementAnalysis(requirements=[])}


def _scan_python_files(work_dir: str) -> List[Dict]:
    """Walk the workspace, return [{'file_path','function_name','code_block'}, ...]
    for every top-level Python function. Runs in executor."""
    functions_to_analyze: List[Dict] = []
    for root, _, files in os.walk(work_dir):
        if "venv" in root:
            continue
        for file in files:
            if file.endswith('.py'):
                file_path = os.path.join(root, file)
                relative_path = os.path.relpath(file_path, work_dir)
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                try:
                    tree = ast.parse(content)
                    for node in ast.walk(tree):
                        if isinstance(node, ast.FunctionDef):
                            code_block = ast.get_source_segment(content, node)
                            if code_block:
                                functions_to_analyze.append({
                                    "file_path": relative_path,
                                    "function_name": node.name,
                                    "code_block": code_block,
                                })
                except SyntaxError:
                    logger.warning(f"Could not parse {relative_path} with AST, skipping.")
    return functions_to_analyze


async def find_and_analyze_code(state: SuperAgentState):
    logger.info("Finding and analyzing code functions...")
    blog("Analyzing codebase functions...")

    work_dir = state['work_dir']
    loop = asyncio.get_running_loop()

    # Phase M.5 — dispatch through the language runner. State["language"] is set
    # by detect_language; default to python for backwards compatibility.
    lang = state.get("language") or "python"
    try:
        runner = get_runner(lang)
        scanned_units = await loop.run_in_executor(None, runner.scan_files, work_dir)
        functions_to_analyze = [
            {"file_path": u.file_path, "function_name": u.function_name, "code_block": u.code_block}
            for u in scanned_units
        ]
    except Exception as exc:
        logger.warning(f"Runner-based scan failed ({exc}); falling back to AST scan")
        functions_to_analyze = await loop.run_in_executor(None, _scan_python_files, work_dir)

    logger.info(f"Found {len(functions_to_analyze)} functions to analyze.")
    blog(f"Found {len(functions_to_analyze)} functions to analyze")

    # Post-MVP Phase 7 — PR-scoped analysis filter. When testing was invoked
    # against a PR branch (orchestrator path or user explicit clone), restrict
    # the LLM analysis to functions in files dev actually changed. Two wins:
    #   - Smaller LLM prompt → no more empty TestPlans on big repos like
    #     carelon (1227 stmts / 45 funcs → 50-300 funcs of analysis JSON
    #     was overflowing the model and yielding `{}`).
    #   - Generated tests focus on the diff, not the whole codebase.
    # Trigger the PR-scoped filter when either:
    #   a) pr_branch is set (explicit PR clone), OR
    #   b) test_scope="feature_only" (user chose "new feature only" in the scope question)
    pr_branch = state.get("pr_branch")
    if not pr_branch and state.get("test_scope") == "feature_only":
        pr_branch = state.get("upstream_development", {}).get("branch_name")
    if pr_branch and functions_to_analyze:
        try:
            from agents_orchestrator.testing_agent.tools.pr_diff import detect_default_branch, changed_files
            base = await loop.run_in_executor(None, detect_default_branch, work_dir)
            changed = set(await loop.run_in_executor(None, changed_files, work_dir, base))
            if changed:
                before = len(functions_to_analyze)
                filtered = [f for f in functions_to_analyze if f["file_path"] in changed]
                if filtered:
                    functions_to_analyze = filtered
                    blog(
                        f"PR-scoped: filtered to {len(functions_to_analyze)}/{before} functions "
                        f"in {len(changed)} files changed vs {base}"
                    )
                else:
                    logger.info(
                        f"PR-scoped: filter would empty the analysis "
                        f"({before} funcs, but none in changed files {sorted(changed)[:5]}); "
                        "keeping full analysis"
                    )
        except Exception as exc:
            logger.warning(f"PR-scoped filter failed ({exc}); using full analysis")

    if not functions_to_analyze:
        logger.warning("No functions found to analyze.")
        blog("No functions found in codebase", level="WARNING")
        return {"code_analysis": CodeAnalysis(functions=[])}

    # Phase 1.6 — Run all per-function analyses CONCURRENTLY.
    analyzer_llm = get_llm().with_structured_output(FunctionInfo, method="function_calling")

    async def _analyze_one(function_data):
        prompt = (
            f"Analyze this Python function from file `{function_data['file_path']}`. "
            f"Set file_path to exactly '{function_data['file_path']}' (do NOT invent it).\n\n"
            f"{function_data['code_block']}"
        )
        try:
            result = await loop.run_in_executor(None, analyzer_llm.invoke, prompt)
            # Phase 1.5 — overwrite the LLM's file_path with the AST-known path.
            # We already KNOW the path from the scan, so don't trust the LLM to round-trip it.
            result.file_path = function_data['file_path']
            logger.info(f"Successfully analyzed {function_data['function_name']}.")
            return result
        except Exception as exc:
            logger.error(f"LLM failed to analyze {function_data['function_name']}. Error: {exc}")
            blog(f"Failed to analyze {function_data['function_name']}: {exc}", level="ERROR")
            return None

    # Limit concurrent API calls to avoid 529 Overloaded errors.
    # 5 concurrent calls balances throughput against rate limits.
    _sem = asyncio.Semaphore(5)

    async def _analyze_one_throttled(fd):
        async with _sem:
            return await _analyze_one(fd)

    blog(f"Analyzing {len(functions_to_analyze)} functions (max 5 concurrent)...")
    results = await asyncio.gather(*[_analyze_one_throttled(fd) for fd in functions_to_analyze], return_exceptions=False)
    all_results = [r for r in results if r is not None]

    logger.info(f"Finished analysis. Successfully analyzed {len(all_results)}/{len(functions_to_analyze)} functions.")
    blog(f"Analysis complete: {len(all_results)}/{len(functions_to_analyze)} functions analyzed")
    return {"code_analysis": CodeAnalysis(functions=all_results)}


async def handle_analysis_failure(state: SuperAgentState):
    logger.error("Analysis Failure Handler: The initial code analysis failed.")
    # Post-MVP Phase 1 — when the upstream clone failed gracefully, prefer the
    # specific actionable message over the generic one.
    clone_err = state.get("clone_error_message")
    if clone_err:
        blog("Surfacing upstream-clone failure to user", level="ERROR")
        return {"final_user_message": clone_err}
    blog("Code analysis failed - no test plan can be generated", level="ERROR")
    error_message = "The agent could not analyze the provided codebase. No tests could be generated."
    return {"final_user_message": error_message}
