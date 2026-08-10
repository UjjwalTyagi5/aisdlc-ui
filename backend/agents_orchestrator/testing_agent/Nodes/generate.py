"""Test-code generation + save nodes.

Phase 4a — extracted from `super_agent.py`:
- _clean_llm_code_output    (line 153)
- generate_test_code        (line 496)  — Phase 1.5 prompt rewrite preserved verbatim
- save_generated_code       (line 567)
"""
from __future__ import annotations

import asyncio
import os
from typing import Dict, List

import aiofiles
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate

from agents_orchestrator.testing_agent.config.session_state import SuperAgentState
from agents_orchestrator.testing_agent.config.shared import blog, get_llm, logger
from agents_orchestrator.testing_agent.tools.language_runner import ScannedFunction
from agents_orchestrator.testing_agent.tools.runners import get_runner


def _clean_llm_code_output(raw_output: str) -> str:
    logger.info("Cleaning LLM-generated code output...")
    code_start = raw_output.find("```python")
    if code_start != -1:
        code_end = raw_output.find("```", code_start + len("```python"))
        if code_end != -1:
            return raw_output[code_start + len("```python"):code_end].strip()
    code_start = raw_output.find("```")
    if code_start != -1:
        code_end = raw_output.find("```", code_start + 3)
        if code_end != -1:
            return raw_output[code_start + 3:code_end].strip()
    logger.warning("No markdown fences found in LLM output. Returning raw stripped text.")
    return raw_output.strip()


async def generate_test_code(state: SuperAgentState):
    logger.info("Generating runnable test code...")
    blog("Generating test code...")

    if not state.get("code_analysis") or not state["code_analysis"].functions:
        logger.warning("No code analysis available for test code generation.")
        blog("No function analysis available for code generation", level="WARNING")
        return {"generated_test_code": "# No code generated."}

    analysis = state['code_analysis']
    analysis_json = analysis.model_dump_json(indent=2)

    # Phase M.5 — runner-aware prompt. Each runner emits the language-specific
    # CRITICAL IMPORT RULES + structure block; we append the analysis JSON.
    lang = state.get("language") or "python"
    try:
        runner = get_runner(lang)
        # Bug-fix: previous version passed code_block="" — runners that need to
        # extract namespace/imports from the source (e.g. DotnetRunner) saw empty
        # blocks and fell back to wrong defaults. Re-scan the work_dir so the
        # prompt has access to real source content (file headers + method bodies).
        work_dir = state.get('work_dir')
        if work_dir:
            scanned_full = runner.scan_files(work_dir)
            # Index by (file_path, function_name) so we can align with the LLM analysis
            by_key = {(s.file_path, s.function_name): s for s in scanned_full}
            scanned = []
            for fn in analysis.functions:
                key = (fn.file_path, fn.function_name)
                if key in by_key:
                    scanned.append(by_key[key])
                else:
                    scanned.append(ScannedFunction(
                        file_path=fn.file_path,
                        function_name=fn.function_name,
                        code_block="",
                    ))
        else:
            scanned = [
                ScannedFunction(
                    file_path=fn.file_path,
                    function_name=fn.function_name,
                    code_block="",
                )
                for fn in analysis.functions
            ]
        base_prompt = runner.code_gen_prompt(scanned)
    except Exception as exc:
        logger.warning(f"Falling back to inline Python prompt (runner.code_gen_prompt failed: {exc})")
        modules_to_funcs: Dict[str, List[str]] = {}
        for fn in analysis.functions:
            mod = os.path.splitext(os.path.basename(fn.file_path))[0]
            modules_to_funcs.setdefault(mod, []).append(fn.function_name)
        import_lines = "\n".join(
            f"from {mod} import " + ", ".join(funcs)
            for mod, funcs in modules_to_funcs.items()
        )
        base_prompt = (
            "Write a pytest file for the following code analysis.\n"
            "\n## CRITICAL IMPORT RULES\n"
            f"```\n{import_lines}\n```\n"
            "Do NOT use importlib / try-except imports / sys.path.append / stubs.\n"
            "## Output: ONLY raw Python code, no fences.\n"
        )

    full_prompt_text = base_prompt + "\n## Code analysis\n{function_analysis}\n"
    prompt = ChatPromptTemplate.from_template(full_prompt_text, template_format="f-string")
    code_gen_chain = prompt | get_llm() | StrOutputParser()

    loop = asyncio.get_running_loop()
    raw_code = await loop.run_in_executor(
        None,
        code_gen_chain.invoke,
        {"function_analysis": analysis_json},
    )
    cleaned_code = _clean_llm_code_output(raw_code)

    blog(f"{lang.capitalize()} test code generated successfully")
    return {"generated_test_code": cleaned_code}


async def save_generated_code(state: SuperAgentState):
    """Save the LLM-generated test code to a path the matching language runner
    will discover. Phase M.5 — path depends on state["language"].

    Phase 10 — when state['generated_test_code'] is missing because the new
    fan-out (dispatch_test_types) wrote per-skill files directly, this node is
    a no-op rather than a KeyError. The runner's existing discovery glob
    (test_*.py / *.cs in tests/ / *.test.jsx in src/) picks up every file the
    skills produced.
    """
    logger.info("Saving generated test code to workspace...")
    blog("Saving generated test code...")

    legacy_code = state.get("generated_test_code")
    fan_out_sets = state.get("generated_test_sets") or []

    if not legacy_code:
        if fan_out_sets:
            file_names = [
                os.path.basename(s.get("test_file_path", ""))
                for s in fan_out_sets if s.get("test_file_path")
            ]
            blog(
                f"Fan-out wrote {len(file_names)} test file(s) directly: "
                f"{', '.join(file_names) or '(passthrough only)'}. "
                "save_generated_code is a no-op."
            )
        else:
            blog(
                "No generated_test_code in state and no fan-out test sets — "
                "no test code to save.",
                level="WARNING",
            )
        return {}

    work_dir = state['work_dir']
    lang = state.get("language") or "python"

    # Per-language conventions:
    #   python: pytest auto-discovers files named test_*.py at workspace root
    #   dotnet: dotnet test runs xUnit classes inside the tests/ project — file
    #           must end in .cs and live where the .csproj globs (tests/)
    #   react:  jest matches **/src/**/*.test.{js,jsx,ts,tsx} (per our jest.config.js)
    if lang == "dotnet":
        # Place inside tests/ next to the test csproj — pick a name ending in .cs
        target_dir = os.path.join(work_dir, "tests")
        os.makedirs(target_dir, exist_ok=True)
        test_file_path = os.path.join(target_dir, "GeneratedTests.cs")
    elif lang == "react":
        # jest config testMatch is **/src/**/*.{test,spec}.{js,jsx,ts,tsx}
        target_dir = os.path.join(work_dir, "src")
        os.makedirs(target_dir, exist_ok=True)
        # Use .test.jsx so jest picks it up with babel-jest
        test_file_path = os.path.join(target_dir, "generated.test.jsx")
    else:
        # python (and unknown — safe default)
        test_file_path = os.path.join(work_dir, "test_generated_by_agent.py")

    async with aiofiles.open(test_file_path, "w", encoding="utf-8") as f:
        await f.write(legacy_code)

    logger.info(f"Saved generated {lang} tests to {test_file_path}")
    blog(f"Test code saved ({lang}): {os.path.basename(test_file_path)}")
    return {}
