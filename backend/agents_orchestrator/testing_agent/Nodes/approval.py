"""Approval-gate nodes for staged testing runs."""
from __future__ import annotations

import asyncio
import json
import os
import re
from collections import Counter
from typing import Dict, List

from agents_orchestrator.testing_agent.config.session_state import SuperAgentState
from agents_orchestrator.testing_agent.config.shared import blog, logger


def _plan_summary(state: SuperAgentState) -> str:
    plan = state.get("test_plan")
    cases = getattr(plan, "test_cases", None) or []
    if not cases:
        return "No test cases were generated."
    counts = Counter((getattr(tc, "scenario_type", "") or "test").strip() for tc in cases)
    by_type = ", ".join(f"{count} {name}" for name, count in counts.items())
    return f"{len(cases)} test cases generated ({by_type})."


async def request_unit_generation_approval(state: SuperAgentState):
    message = (
        f"Code analysis and test planning are complete. {_plan_summary(state)}\n\n"
        "Please review the generated test plan. Reply `yes` or `approve` to generate the unit test code. "
        "Reply with changes if you want the plan adjusted before code generation."
    )
    blog("Awaiting user approval before generating unit test code")
    return {
        "final_user_message": message,
        "pending_testing_approval": "generate_unit_code",
        "staged_testing_enabled": True,
    }


def _extract_test_names_from_file(file_path: str) -> list[str]:
    """Return all `def test_*` function names from a generated Python test file."""
    if not file_path or not os.path.exists(file_path):
        return []
    try:
        with open(file_path, "r", encoding="utf-8") as fh:
            return re.findall(r"^def (test_\w+)", fh.read(), re.MULTILINE)
    except Exception:
        return []


def _readable_test_name(fn_name: str) -> str:
    """Convert `test_post_users_happy` → `Post Users Happy`."""
    return fn_name.replace("test_", "").replace("_", " ").title()


def _write_functional_test_plan_md(work_dir: str, cases_by_skill: Dict[str, list[str]]) -> str:
    """Write a human-readable markdown test plan; return the file path."""
    lines = ["# Functional Test Plan\n"]
    total = 0
    for skill, cases in cases_by_skill.items():
        label = skill.replace("_", " ").title()
        lines.append(f"## {label}\n")
        for i, case in enumerate(cases, 1):
            lines.append(f"{i}. **{_readable_test_name(case)}**  \n   `{case}`\n")
        total += len(cases)
    lines.append(f"\n---\n**Total: {total} test case(s)**\n")

    reports_dir = os.path.join(work_dir, "reports")
    os.makedirs(reports_dir, exist_ok=True)
    out_path = os.path.join(reports_dir, "functional_test_plan.md")
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    return out_path


async def request_functional_execution_approval(state: SuperAgentState):
    """Gate: after dispatch_test_types generates functional test code, show the
    test cases to the user and wait for approval before executing them on the URL."""
    generated_sets = state.get("generated_test_sets") or []
    work_dir = state.get("work_dir") or ""

    cases_by_skill: Dict[str, list[str]] = {}
    for item in generated_sets:
        if not isinstance(item, dict):
            continue
        skill_name = item.get("skill_name", "")
        if skill_name not in ("functional_api", "functional_ui"):
            continue
        if skill_name == "functional_ui":
            cases_by_skill[skill_name] = ["ui_browser_automation (Selenium)"]
            continue
        file_path = item.get("test_file_path") or ""
        names = _extract_test_names_from_file(file_path)
        if names:
            cases_by_skill[skill_name] = names

    total = sum(len(v) for v in cases_by_skill.values())
    plan_file: str | None = None

    if work_dir and cases_by_skill:
        try:
            plan_file = _write_functional_test_plan_md(work_dir, cases_by_skill)
            blog(f"Functional test plan written to {os.path.basename(plan_file)}")
        except Exception as exc:
            logger.warning(f"Could not write functional test plan file: {exc}")

    # Build per-skill listing for the chat message
    lines: list[str] = []
    for skill, cases in cases_by_skill.items():
        label = skill.replace("_", " ").title()
        lines.append(f"**{label}** — {len(cases)} test case(s):")
        for case in cases[:12]:
            lines.append(f"  • {_readable_test_name(case)}")
        if len(cases) > 12:
            lines.append(f"  • … and {len(cases) - 12} more")

    target_url = state.get("target_url") or "the configured URL"
    plan_note = (
        f"\n\nThe full test plan has been saved to `{os.path.basename(plan_file)}`"
        " — download it to review before approving."
        if plan_file else ""
    )

    message = (
        f"I've generated **{total} functional test case(s)** ready to run against `{target_url}`.\n\n"
        + "\n".join(lines)
        + plan_note
        + "\n\nReply `yes` or `approve` to execute these tests against the URL, "
        "or `no` to cancel."
    )

    blog(f"Awaiting user approval before running {total} functional test(s) on {target_url}")
    return {
        "final_user_message": message,
        "pending_functional_approval": "run_functional_tests",
        "functional_test_plan_file": plan_file,
    }


async def generate_ui_test_plan_and_request_approval(state: SuperAgentState):
    """For the direct ui_test path: use Claude to generate structured test cases
    from the user's specific request, present them for approval, and store the
    JSON representation so the Selenium agent executes exactly those cases."""
    from agents_orchestrator.testing_agent.config.shared import build_llm
    from langchain_core.output_parsers import StrOutputParser

    target_url = state.get("target_url") or ""
    user_prompt = (state.get("user_prompt") or "").strip()
    upstream_req = state.get("upstream_requirements") or {}
    upstream_design = state.get("upstream_design") or {}

    context_hints = ""
    if isinstance(upstream_req, dict):
        stories = (upstream_req.get("stories") or [])[:5]
        if stories:
            context_hints += f"\nRelevant user stories: {stories}"
    if isinstance(upstream_design, dict) and upstream_design.get("api_contracts"):
        context_hints += "\nAPI contracts from design agent are available."

    # Ask Claude for a JSON array so the approved test cases are EXACTLY what
    # the Selenium agent will execute — no re-generation at execution time.
    json_prompt = (
        f"You are a senior QA engineer. The user has asked:\n"
        f"\"{user_prompt}\"\n\n"
        f"Application URL: {target_url}\n"
        f"{context_hints}\n\n"
        "Generate ONLY test cases that directly address the user's request. "
        "Do NOT add generic tests (home page load, login, etc.) unless explicitly asked.\n\n"
        "Return a JSON array. Each item must have exactly these keys:\n"
        "  \"id\"          — short ID, e.g. \"TC-UI-01\"\n"
        "  \"description\" — concise title, e.g. \"Create New Case — Happy Path\"\n"
        "  \"goal\"        — one sentence describing what to do and what to verify\n\n"
        "Generate as many cases as needed to cover the request fully (happy path + "
        "key validation/edge cases). Output ONLY the raw JSON array, no markdown fences, "
        "no extra text."
    )

    blog("Generating UI test plan for user review...")
    planned_cases: List[Dict] = []
    loop = asyncio.get_running_loop()
    try:
        llm = build_llm(max_tokens=4000)
        chain = llm | StrOutputParser()
        raw_json = await loop.run_in_executor(None, chain.invoke, json_prompt)
        # Strip markdown fences if the LLM wrapped the array anyway
        raw_json = raw_json.strip()
        if raw_json.startswith("```"):
            raw_json = re.sub(r"^```[a-z]*\n?", "", raw_json)
            raw_json = re.sub(r"\n?```$", "", raw_json.strip())
        parsed = json.loads(raw_json)
        if isinstance(parsed, list):
            planned_cases = [
                c for c in parsed
                if isinstance(c, dict) and c.get("id") and c.get("description")
            ]
    except Exception as exc:
        logger.error(f"UI test plan JSON generation failed: {exc}")

    # Fallback: synthesise a single case so execution always has something to run
    if not planned_cases:
        planned_cases = [{
            "id": "TC-UI-01",
            "description": user_prompt[:80] or "Requested UI flow",
            "goal": user_prompt or "Complete the requested user flow on the application.",
        }]

    # Build human-readable plan from the same JSON so chat == execution
    plan_lines: List[str] = [f"**Test cases for:** {target_url}\n"]
    for tc in planned_cases:
        plan_lines.append(
            f"**{tc['id']}**: {tc['description']}\n"
            f"> {tc.get('goal', '')}\n"
        )
    plan_text = "\n".join(plan_lines)

    # Save plan file when work_dir is available
    work_dir = state.get("work_dir") or ""
    plan_file: str | None = None
    if work_dir:
        try:
            reports_dir = os.path.join(work_dir, "reports")
            os.makedirs(reports_dir, exist_ok=True)
            plan_file = os.path.join(reports_dir, "ui_test_plan.md")
            md_lines = [f"# UI Test Plan\n\n**Target URL:** {target_url}\n"]
            for tc in planned_cases:
                md_lines.append(
                    f"## {tc['id']}: {tc['description']}\n\n"
                    f"**Goal:** {tc.get('goal', '')}\n"
                )
            with open(plan_file, "w", encoding="utf-8") as fh:
                fh.write("\n".join(md_lines))
            blog(f"UI test plan written to {os.path.basename(plan_file)}")
        except Exception as exc:
            logger.warning(f"Could not write UI test plan file: {exc}")

    plan_note = (
        f"\n\nFull plan saved to `{os.path.basename(plan_file)}`."
        if plan_file else ""
    )

    message = (
        f"Here are the **{len(planned_cases)} UI test case(s)** I'll run "
        f"against **{target_url}**:\n\n"
        f"{plan_text}"
        + plan_note
        + "\n\nReply `yes` or `approve` to execute these using the browser automation agent. "
        "Reply `no` to cancel."
    )

    ui_test_plan_json = json.dumps(planned_cases)
    blog(f"Awaiting user approval before running {len(planned_cases)} UI test(s) on {target_url}")
    return {
        "final_user_message": message,
        "pending_functional_approval": "run_ui_tests",
        "functional_test_plan_file": plan_file,
        "ui_test_plan_json": ui_test_plan_json,
    }


async def request_unit_execution_approval(state: SuperAgentState):
    generated_sets = state.get("generated_test_sets") or []
    generated_files: list[str] = []
    for item in generated_sets:
        if not isinstance(item, dict):
            continue
        generated_files.extend(item.get("test_file_paths") or [])
        if item.get("test_file_path"):
            generated_files.append(item["test_file_path"])
    unique_count = len({path for path in generated_files if path})
    scenario_count = sum(
        int(item.get("scenario_count") or 0)
        for item in generated_sets
        if isinstance(item, dict)
    )
    if unique_count:
        generated_text = f"Generated {unique_count} unit test file(s) with {scenario_count} runnable scenario(s)."
    else:
        generated_text = "Unit test generation finished, but no generated test files were recorded."
    message = (
        f"{generated_text}\n\n"
        "Please review the generated unit test files. Reply `yes` or `approve` to run the unit tests and collect coverage."
    )
    blog("Awaiting user approval before running generated unit tests")
    return {
        "final_user_message": message,
        "pending_testing_approval": "run_unit_tests",
        "staged_testing_enabled": True,
    }
