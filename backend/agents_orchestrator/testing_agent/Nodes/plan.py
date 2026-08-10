"""Test-plan generation node.

Phase 4a — extracted from `super_agent.py:466 generate_test_plan`.
The graph registers this same function under three node names:
- `generate_test_plan`        (canonical)
- `generate_plan_from_reqs`   (alias used after analyze_requirements)
- `generate_plan_from_code`   (alias used after find_and_analyze_code)
"""
from __future__ import annotations

import asyncio

from agents_orchestrator.testing_agent.config.session_state import SuperAgentState, TestPlan
from agents_orchestrator.testing_agent.config.shared import blog, build_llm, logger


async def generate_test_plan(state: SuperAgentState):
    logger.info("Generating human-readable test plan...")
    blog("Generating test plan...")

    analysis_json = ""
    if state.get("code_analysis") and state["code_analysis"].functions:
        analysis_json = state['code_analysis'].model_dump_json(indent=2)
    elif state.get("requirement_analysis") and state["requirement_analysis"].requirements:
        analysis_json = state['requirement_analysis'].model_dump_json(indent=2)
    else:
        logger.warning("No analysis available to generate test plan.")
        blog("No analysis available for test plan generation", level="WARNING")
        return {"test_plan": TestPlan(test_cases=[])}

    # Stronger prompt — Anthropic's structured-output sometimes returns {} when the
    # prompt is too terse; spelling out "test_cases must be a non-empty list of objects"
    # in the prompt itself reduces empty-output validation errors.
    #
    # Field names MUST match the TestCase pydantic model in config/session_state.py:
    #   test_case_id, feature_or_function_tested, test_summary, scenario_type,
    #   test_steps, test_data, expected_result
    # Earlier prompt asked for the wrong names (id, function_name, description,
    # inputs, expected_output, test_type) — Pydantic then rejected the LLM's
    # output as "test_cases Field required", which is why empty TestPlans kept
    # appearing on real runs.
    prompt = (
        "Create a comprehensive test plan based on this analysis. Return a TestPlan "
        "object with a non-empty `test_cases` list. Each TestCase must include the "
        "following EXACT field names:\n"
        "  - test_case_id     (e.g. 'TC-REQ-01' or 'TC-FUNC-03')\n"
        "  - feature_or_function_tested  (the function or requirement under test)\n"
        "  - test_summary     (short descriptive title)\n"
        "  - scenario_type    (one of: 'Happy Path', 'Error Case', 'Edge Case')\n"
        "  - test_steps       (numbered list of actions to perform, as a string)\n"
        "  - test_data        (concrete input values to use)\n"
        "  - expected_result  (the expected outcome or error)\n"
        "\n"
        f"Analysis:\n{analysis_json}"
    )
    # Limit to first 20 functions — the LLM skills (unit, integration, etc.)
    # generate the actual test code from the full analysis anyway. The TestPlan
    # is only a human-readable summary; 20 test cases is plenty and keeps
    # the prompt + output well under the model's token limits.
    try:
        import json as _json
        analysis_obj = _json.loads(analysis_json)
        funcs = analysis_obj.get("functions", [])
        if len(funcs) > 20:
            analysis_obj["functions"] = funcs[:20]
            analysis_json = _json.dumps(analysis_obj)
            prompt = prompt[:prompt.rfind("Analysis:")] + f"Analysis:\n{analysis_json}"
    except Exception:
        pass  # if slicing fails, proceed with original prompt

    # Hard cap at 40k chars after slicing
    if len(prompt) > 40_000:
        prompt = prompt[:40_000] + "\n\n[Analysis truncated]"

    # Create a dedicated BYOK LLM instance with higher max_tokens for this call.
    # The shared default uses 8192 which is too small for structured TestPlan output.
    # build_llm reads the run's resolved model from the contextvar (fail-closed).
    generator_llm = build_llm(max_tokens=16000).with_structured_output(
        TestPlan, method="function_calling")

    try:
        loop = asyncio.get_running_loop()
        test_plan = await loop.run_in_executor(None, generator_llm.invoke, prompt)
        logger.info(f"Test Plan generated with {len(test_plan.test_cases)} cases.")
        blog(f"Generated {len(test_plan.test_cases)} test cases")
        return {"test_plan": test_plan}
    except Exception as exc:
        # Post-MVP fix — Pydantic ValidationError (LLM returned {} or partial dict)
        # used to surface as a scary "Field required" stack trace in the user's chat.
        # Distinguish: LLM-output validation failure vs. real errors (network, API).
        # Use the matching log level on BOTH the python logger AND the blog
        # broadcast — otherwise the python logger.error message is broadcast
        # to the activity feed in addition to the friendly blog warning, so
        # the user sees both the scary ERROR + the friendly WARNING.
        from pydantic import ValidationError
        is_validation_err = isinstance(exc, ValidationError)
        if is_validation_err:
            log_msg = (
                "LLM returned no test cases (empty TestPlan). "
                "Proceeding with empty plan — code-level test generation downstream "
                "will still produce executable tests from the analysis."
            )
            logger.warning(f"Test plan generation: LLM returned empty/partial TestPlan ({exc.error_count()} validation errors)")
            blog(log_msg, level="WARNING")
        else:
            log_msg = f"Test plan generation failed: {exc}"
            logger.error(f"ERROR during test plan generation: {exc}")
            blog(log_msg, level="ERROR")
        return {"test_plan": TestPlan(test_cases=[])}
