"""Intent classification + greeting / follow-up / file-read nodes.

Phase 4a — extracted from `super_agent.py` IntentDrivenAgent methods at:
- classify_intent           (line 171)
- handle_greeting           (line 242)
- handle_follow_up_query    (line 255)
- read_input_content        (line 286)

Behaviour preserved verbatim. The only structural change is dropping the
`self` arg — each node is now a module-level `async def`.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Dict, Optional

import aiofiles
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate

from agents_orchestrator.testing_agent.config.session_state import SuperAgentState
from agents_orchestrator.testing_agent.config.shared import (
    blog, get_llm, get_session_id, logger, record_response_usage,
)


_VCS_CI_HOSTS = (
    "dev.azure.com",
    "visualstudio.com",
    "github.com",
    "gitlab.com",
    "bitbucket.org",
    "jenkins.",
    "travis-ci.",
    "circleci.com",
    "app.circleci.com",
)

def _extract_url(text: str) -> Optional[str]:
    url_pattern = re.compile(r'https?://[^\s/$.?#].[^\s]*')
    for match in url_pattern.finditer(text or ""):
        url = match.group(0).rstrip(".,;:!?)]}'\"")
        if not any(host in url for host in _VCS_CI_HOSTS):
            return url
    return None


_TEST_TYPE_ALIASES = {
    "unit": "unit",
    "unit testing": "unit",
    "code": "unit",
    "code testing": "unit",
    "functional": "functional",
    "functional testing": "functional",
    "ui": "functional",
    "ui testing": "functional",
    "browser": "functional",
    "browser testing": "functional",
    "e2e": "functional",
    "end to end": "functional",
    "end-to-end": "functional",
    "api": "api",
    "api testing": "api",
    "functional api": "api",
    "endpoint": "api",
    "endpoint testing": "api",
}


def _normalize_selected_test_types(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        raw_parts = re.split(r"[,;/|+]+", value)
    elif isinstance(value, (list, tuple, set)):
        raw_parts = list(value)
    else:
        raw_parts = []

    selected: list[str] = []
    for raw in raw_parts:
        item = str(raw or "").strip().lower()
        normalized = _TEST_TYPE_ALIASES.get(item)
        if normalized and normalized not in selected:
            selected.append(normalized)
    return selected


def _is_approval_response(value: str | None) -> bool:
    text = (value or "").strip().lower()
    if text in {
        "y",
        "yes",
        "approve",
        "approved",
        "go ahead",
        "proceed",
        "continue",
        "run it",
        "generate it",
        "generate code",
        "generate test code",
        "generate unit test code",
        "generate the test code",
        "generate the unit test code",
    }:
        return True
    return bool(
        re.match(r"^(yes|yep|yeah|approve|approved|proceed|continue)\b", text)
        or "go ahead" in text
        or "looks good" in text
        or re.search(r"\bgenerate\b.*\b(unit\s+)?test\s+code\b", text)
        or re.search(r"\bgenerate\b.*\b(unit\s+)?tests?\b", text)
    )


def _is_rejection_response(value: str | None) -> bool:
    text = (value or "").strip().lower()
    return text in {"n", "no", "stop", "cancel", "reject", "rejected"} or bool(
        re.match(r"^(no|nope|stop|cancel|reject)\b", text)
    )


def _infer_test_types_from_prompt(prompt: str) -> list[str]:
    lower = f" {(prompt or '').lower()} "
    selected: list[str] = []
    if re.search(r"\b(unit|unit testing|code tests?|code testing)\b", lower):
        selected.append("unit")
    if "functional api" not in lower and re.search(r"\b(functional|functional testing|ui testing|browser testing|e2e|end[- ]to[- ]end)\b", lower):
        selected.append("functional")
    if re.search(r"\b(api|api testing|functional api|endpoint tests?|endpoint testing)\b", lower):
        selected.append("api")
    return selected


def _wants_staged_unit_testing(prompt: str) -> bool:
    """Return True unless the user explicitly asks to execute tests now.

    The default chatbot flow is staged:
    plan -> user review -> generate unit test code -> user review -> run tests.
    Users can still bypass staging with explicit execution language such as
    "run unit tests now" or "execute unit tests and coverage".
    """
    lower = f" {(prompt or '').lower()} "
    execute_now_patterns = (
        r"\brun\b.*\bunit tests?\b.*\bnow\b",
        r"\bexecute\b.*\bunit tests?\b",
        r"\bunit testing\b.*\bcoverage\b",
        r"\bend to end\b.*\bunit\b.*\btesting\b",
        r"\bgenerate\b.*\brun\b.*\bunit tests?\b",
    )
    if any(re.search(pattern, lower) for pattern in execute_now_patterns):
        return False
    return True


def _testing_scope_prompt(state: SuperAgentState, reason: str = "") -> str:
    upstream_dev = state.get("upstream_development") or {}
    branch = upstream_dev.get("branch_name") if isinstance(upstream_dev, dict) else None
    target_hint = f" for branch `{branch}`" if branch else ""
    prefix = f"{reason}\n\n" if reason else ""
    return (
        f"{prefix}What testing would you like me to run{target_hint}?\n\n"
        "Choose one or more:\n"
        "- `unit` - generate and run code-level unit tests\n"
        "- `functional` - browser/UI functional testing, requires an application URL\n"
        "- `api` - live API endpoint testing, requires an API base URL\n\n"
        "Examples: `unit`, `unit + api https://api.example.com`, "
        "or `functional https://app.example.com`."
    )


def _apply_selected_testing_contract(selected: list[str], target_url: Optional[str]) -> dict:
    result: dict = {
        "selected_test_types": selected,
        "test_scope": "full" if "unit" in selected else None,
        "api_scope": "functional" if "api" in selected else None,
        "ui_scope": "full" if "functional" in selected else None,
        "awaiting_scope": False,
    }
    if target_url:
        result["target_url"] = target_url
    return result


def _synthesise_upstream_development_from_hints(merged: Dict[str, str]) -> Optional[dict]:
    """Build the dev-handoff shape used by workspace setup from parsed hints."""
    repo = (merged.get("repo") or "").strip()
    branch = (merged.get("branch") or "").strip()
    if not repo or not branch:
        return None

    project = (merged.get("project") or repo).strip()
    repo_url = (merged.get("repo_url") or "").strip()
    if not repo_url:
        # No org here: the ADO org is tenant data (secret store "ado-org-url"),
        # not process configuration, and this helper is sync. Callers that need a
        # real URL pass repo_url explicitly; this placeholder only has to be shaped
        # like an ADO URL for the downstream parser.
        repo_url = f"https://dev.azure.com/_/{project}/_git/{repo}"

    return {
        "repo_url": repo_url,
        "branch_name": branch,
        "project": project,
        "repo": repo,
        "_source": merged.get("_source") or "context_fallback",
    }


def _clear_previous_run_artifacts() -> dict:
    """Clear artifacts from a prior task in the same chat session.

    The agent supports continuing from prior state, but a new explicit
    testing request must not inherit old unit plans into UI-only runs or old
    UI results into unit artifacts.
    """
    return {
        "code_analysis": None,
        "requirement_analysis": None,
        "test_plan": None,
        "generated_test_code": None,
        "test_execution_summary": None,
        "ui_test_results": None,
        "ui_tests_completed": False,
        "generated_test_sets": [],
        "skill_failures": [],
        "aggregated_results": None,
        "defect_log": [],
        "qa_report_html_path": None,
        "qa_report_pdf_path": None,
        "test_run_attempted": False,
        "test_runner_exit_code": None,
        "test_runner_stdout": None,
        "test_runner_stderr": None,
        "pr_coverage": None,
    }


async def pull_upstream_context(state: SuperAgentState):
    """Phase 5 — read upstream req/design/dev artifacts so testing can run
    without an explicit upload when called as part of an orchestrator flow.

    Resolution order (mirrors design + dev agents):
      1. `pop_cached_context(session_id)` — pre-warmed string from the
         previous agent's `handoff_router.handle()` call.
      2. `await build_context(session_id, "testing")` — fresh fetch + format.
      3. `fetch_session_artifacts(session_id)` — raw dicts so we can
         hydrate `RequirementAnalysis` directly without an LLM call.

    Idempotent: skips if `state["upstream_loaded"]` is already True.
    """
    if state.get("upstream_loaded"):
        return {}

    session_id = get_session_id()
    if not session_id or session_id == "default_session":
        return {"upstream_loaded": True}

    # Lazy imports — keep the testing-agent runnable even if these shared infra
    # modules fail to load (e.g. unit-test envs).
    try:
        from config.context_broker import build_context
        from config.orchestrator_state_client import fetch_session_artifacts
    except ImportError as exc:
        logger.warning(f"Phase 5: context-broker imports unavailable ({exc}) — skipping upstream pull")
        return {"upstream_loaded": True}

    # 1. Build context from session artifacts
    context_str: Optional[str] = None
    try:
        context_str = await build_context(session_id, "testing")
    except Exception as exc:
        logger.warning(f"Phase 5: build_context failed: {exc}")
        context_str = None

    # 2. Raw artifacts dict — used to short-circuit analyze_requirements.
    # Post-MVP Phase 2 — race-retry: when invoked from the orchestrator right
    # after dev finishes, dev's async artifact write may not have committed
    # before testing starts. Retry up to 3 times with 500ms backoff IF this
    # looks like an orchestrator-driven session (no explicit clone_target /
    # input_file_path) and development_artifacts is missing on the first try.
    raw_artifacts: Dict = {}
    is_orch_driven = state.get("clone_target") is None and state.get("input_file_path") is None
    for attempt in range(3):
        try:
            raw_artifacts = await fetch_session_artifacts(session_id) or {}
        except Exception as exc:
            logger.warning(f"Phase 5: fetch_session_artifacts failed (attempt {attempt+1}/3): {exc}")
            raw_artifacts = {}
        if not is_orch_driven:
            break  # don't burn time retrying when the user uploaded a file
        if raw_artifacts.get("development_artifacts"):
            break  # got it
        if attempt < 2:
            await asyncio.sleep(0.5)  # ~500ms backoff before next try
    if is_orch_driven and not raw_artifacts.get("development_artifacts"):
        logger.warning(f"Phase 5: no development_artifacts after 3 retries for session {session_id}")

    upstream_requirements = raw_artifacts.get("requirements_payload") or None
    upstream_design = raw_artifacts.get("design_artifacts") or None
    upstream_development = raw_artifacts.get("development_artifacts") or None

    # Phase 8.9d — chat-history fallback. When orchestrator-driven AND the
    # dev's development_artifacts row never landed in Postgres (broken write
    # somewhere upstream, see Â§32.9), mine the orchestrator's prior message
    # list (passed through previous_state by the API layer) for hints from
    # dev's user-facing chat output. extract_dev_chat_hints handles partial
    # extraction; we accumulate newest-first so the most recent dev mention
    # wins. If we can synthesise repo + branch (project optional, defaults
    # to repo), we synthesise a faux upstream_development dict so the
    # downstream classify_intent / setup_workspace path picks it up
    # naturally.
    if is_orch_driven and not upstream_development:
        try:
            from agents_orchestrator.testing_agent.tools.ado_clone import extract_dev_chat_hints

            candidate_contents: list[str] = []
            for content in (state.get("user_prompt"), context_str):
                if isinstance(content, str) and content.strip():
                    candidate_contents.append(content)

            chat_history = state.get("orchestrator_chat_history") or []
            for msg in reversed(chat_history):
                content = (msg.get("content") if isinstance(msg, dict) else None) or ""
                if content:
                    candidate_contents.append(content)

            merged: Dict[str, str] = {"_source": "context_fallback"}
            for content in candidate_contents:
                hints = extract_dev_chat_hints(content)
                for k, v in hints.items():
                    merged.setdefault(k, v)
                if "branch" in merged and "repo" in merged:
                    break

            synthesized = _synthesise_upstream_development_from_hints(merged)
            if synthesized:
                upstream_development = synthesized
                logger.info(
                    "Phase 8.9d: recovered dev context from orchestrator context - "
                    "repo=%s branch=%s project=%s",
                    synthesized.get("repo"),
                    synthesized.get("branch_name"),
                    synthesized.get("project"),
                )
                blog(
                    f"Recovered dev context from orchestrator context: "
                    f"branch {synthesized.get('branch_name')} of "
                    f"{synthesized.get('project')}/{synthesized.get('repo')} "
                    f"(development_artifacts row missing - used context fallback)",
                    level="WARNING",
                )
        except Exception as exc:
            logger.warning(f"Phase 8.9d context fallback failed: {exc}")

    n_req = (
        len((upstream_requirements or {}).get("stories") or [])
        if isinstance(upstream_requirements, dict) else 0
    )
    if n_req:
        logger.info(f"Phase 5: hydrated {n_req} upstream requirements for session {session_id}")
        blog(f"Loaded {n_req} requirements from upstream session context")
    elif context_str:
        logger.info(f"Phase 5: pre-warmed context block loaded for session {session_id} ({len(context_str)} chars)")
        blog("Loaded pre-warmed context block from upstream agent")

    # Post-MVP Phase 2 — explicit diagnostic blogs so the user sees WHY the
    # agent did/didn't auto-clone the development branch. Avoids the silent
    # fall-through to "expecting upload" that the user mistook for "looking
    # for an Excel file from the development agent."
    if is_orch_driven:
        if upstream_development is None:
            blog(
                "No development handoff found in this session — testing will require "
                "either an uploaded file or an explicit clone target. "
                "If the development agent ran already, its artifact handoff may have failed.",
                level="WARNING",
            )
        elif not isinstance(upstream_development, dict):
            blog(
                f"Development handoff is not a dict (got {type(upstream_development).__name__}) — "
                "testing will require an uploaded file.",
                level="WARNING",
            )
        elif not upstream_development.get("repo_url"):
            present_keys = list(upstream_development.keys())
            blog(
                f"Development handoff present but missing `repo_url` "
                f"(keys received: {present_keys}). Testing cannot auto-clone — "
                f"please attach a file or provide a clone target.",
                level="WARNING",
            )
        elif not upstream_development.get("branch_name"):
            blog(
                f"Development handoff has repo_url={upstream_development.get('repo_url')} "
                f"but no branch_name — defaulting to 'main' (may fail if dev pushed elsewhere).",
                level="WARNING",
            )
        else:
            blog(
                f"Development handoff: will clone "
                f"{upstream_development.get('repo_url')} @ {upstream_development.get('branch_name')}"
            )

    return {
        "upstream_requirements": upstream_requirements,
        "upstream_design": upstream_design,
        "upstream_development": upstream_development,
        "upstream_context_str": context_str,
        "upstream_loaded": True,
    }


async def classify_intent(state: SuperAgentState):
    logger.info("Classifying user intent...")
    blog("Classifying user intent...")

    user_prompt = state.get("user_prompt", "")
    file_path = state.get("input_file_path")
    history = state.get("chat_history")
    # Fix (b): a test type named explicitly in THIS prompt overrides a stale
    # checkpointed `selected_test_types` from a prior run (e.g. "now run api
    # testing" after a completed unit run must not silently reuse "unit").
    # Only fall back to the checkpoint when the current prompt names no type
    # at all — this keeps the awaiting-scope resume path (prompt is a bare
    # URL) working, since a URL infers no test type.
    prompt_selected_types = _infer_test_types_from_prompt(user_prompt or "")
    selected_types = prompt_selected_types or _normalize_selected_test_types(state.get("selected_test_types"))
    explicit_target_url = state.get("target_url") or _extract_url(user_prompt or "")

    # â"€â"€ Functional-test approval gate (api/UI tests) â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€
    pending_functional = state.get("pending_functional_approval")
    if pending_functional:
        if _is_approval_response(user_prompt):
            if pending_functional == "run_functional_tests":
                blog("User approved functional test execution")
                return {
                    "classified_intent": "approve_run_functional_tests",
                    "pending_functional_approval": None,
                    "functional_tests_approved": True,
                    "final_user_message": None,
                }
            if pending_functional == "run_ui_tests":
                blog("User approved UI test execution")
                return {
                    "classified_intent": "approve_run_ui_tests",
                    "pending_functional_approval": None,
                    "functional_tests_approved": True,
                    "final_user_message": None,
                }
        if _is_rejection_response(user_prompt):
            action = "UI tests" if pending_functional == "run_ui_tests" else "functional tests"
            message = f"Cancelled. The {action} were not run."
            blog(message)
            return {
                "classified_intent": "greeting",
                "pending_functional_approval": None,
                "functional_tests_approved": False,
                "final_user_message": message,
            }
        waiting_for = "UI tests" if pending_functional == "run_ui_tests" else "functional tests"
        message = (
            f"I'm waiting for your approval to run the {waiting_for}. "
            "Reply `yes`/`approve` to proceed, or `no` to cancel."
        )
        blog(message)
        return {"classified_intent": "greeting", "final_user_message": message}

    # â"€â"€ Unit-test staged approval gate â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€
    pending_approval = state.get("pending_testing_approval")
    if pending_approval:
        if _is_approval_response(user_prompt):
            if pending_approval == "generate_unit_code":
                blog("User approved unit test code generation")
                return {
                    "classified_intent": "approve_generate_unit_code",
                    "pending_testing_approval": None,
                    "staged_testing_enabled": True,
                    "selected_test_types": selected_types or ["unit"],
                    "final_user_message": None,
                }
            if pending_approval == "run_unit_tests":
                blog("User approved unit test execution")
                return {
                    "classified_intent": "approve_run_unit_tests",
                    "pending_testing_approval": None,
                    "staged_testing_enabled": True,
                    "selected_test_types": selected_types or ["unit"],
                    "final_user_message": None,
                }
        if _is_rejection_response(user_prompt):
            message = "Stopped the staged unit testing flow. No further test generation or execution was run."
            blog(message)
            return {
                "classified_intent": "greeting",
                "pending_testing_approval": None,
                "staged_testing_enabled": False,
                "final_user_message": message,
            }
        message = (
            "I am waiting for approval to "
            + ("generate the unit test code." if pending_approval == "generate_unit_code" else "run the unit tests.")
            + " Reply `yes`/`approve` to continue, or `no` to stop."
        )
        blog(message)
        return {"classified_intent": "greeting", "final_user_message": message}

    if state.get("staged_testing_enabled") and _is_approval_response(user_prompt):
        generated_sets = state.get("generated_test_sets") or []
        if state.get("test_plan") and not generated_sets:
            blog("Recovered staged approval from existing test plan; resuming unit test code generation")
            return {
                "classified_intent": "approve_generate_unit_code",
                "pending_testing_approval": None,
                "staged_testing_enabled": True,
                "selected_test_types": selected_types or ["unit"],
                "final_user_message": None,
            }
        if generated_sets and not state.get("test_run_attempted"):
            blog("Recovered staged approval from generated test files; resuming unit test execution")
            return {
                "classified_intent": "approve_run_unit_tests",
                "pending_testing_approval": None,
                "staged_testing_enabled": True,
                "selected_test_types": selected_types or ["unit"],
                "final_user_message": None,
            }

    # I3 — selected_test_types persists in the checkpoint after a run. A follow-up
    # message that does NOT itself request a test type must be answered as a follow-up,
    # not silently re-run the whole suite. Detect: type came only from the checkpoint
    # (this turn's prompt names no type) + a prior run already completed → clear the
    # sticky scope and route to the follow-up handler.
    prior_run_completed = bool(
        state.get("test_run_attempted") or state.get("aggregated_results")
        or state.get("ui_tests_completed") or state.get("test_execution_summary")
    )
    if (selected_types and not prompt_selected_types
            and prior_run_completed and not explicit_target_url):
        blog("Prior test run complete; treating this message as a follow-up question.")
        return {
            "classified_intent": "follow_up_query",
            "selected_test_types": None,
            "test_scope": None,
            "ui_scope": None,
            "api_scope": None,
            "awaiting_scope": False,
        }

    if selected_types:
        upstream_dev_for_selection = state.get("upstream_development") or {}
        clone_target_for_selection = state.get("clone_target") or {}
        # A PREPARED WORKSPACE IS CODE TO TEST. setup_workspace treats an existing
        # work_dir as the highest-priority source — it is the Copilot's shared run
        # workspace (`ps.work_dir`), already cloned once for the whole pipeline — but
        # this gate did not count it, so an orchestrator handoff that passed the
        # workspace instead of a repo/branch was answered "Unit testing needs code to
        # test" with the checkout sitting right there. Checked on disk, matching
        # setup_workspace's own guard, so a stale path still falls through to the
        # upload prompt rather than starting a run against a directory that is gone.
        prepared_dir = state.get("work_dir")
        has_prepared_workspace = (
            isinstance(prepared_dir, str) and bool(prepared_dir) and os.path.isdir(prepared_dir)
        )
        has_code_target = bool(
            file_path
            or has_prepared_workspace
            or (isinstance(upstream_dev_for_selection, dict) and upstream_dev_for_selection.get("repo_url"))
            or (isinstance(clone_target_for_selection, dict) and clone_target_for_selection.get("repo"))
        )
        if "unit" in selected_types and not has_code_target:
            message = (
                "Unit testing needs code to test. Upload a source file or zip, "
                "or provide the repo and branch from the development handoff."
            )
            blog(message)
            return {
                "classified_intent": "greeting",
                "awaiting_scope": True,
                "final_user_message": message,
                "selected_test_types": selected_types,
            }
        needs_url = any(t in {"functional", "api"} for t in selected_types)
        if needs_url and not explicit_target_url:
            message = _testing_scope_prompt(
                state,
                "I have the testing type, but functional/API testing needs a reachable URL.",
            )
            blog(message)
            return {
                "classified_intent": "greeting",
                "awaiting_scope": True,
                "final_user_message": message,
                "selected_test_types": selected_types,
            }

        contract = _apply_selected_testing_contract(selected_types, explicit_target_url)
        if "unit" in selected_types:
            intent = (
                "single_file_test"
                if file_path and file_path.endswith(('.py', '.cs', '.jsx', '.tsx', '.js', '.ts'))
                else "full_test"
            )
        elif "api" in selected_types:
            intent = "api_ui_only"
        else:
            intent = "ui_test"
        logger.info("Selected testing contract: types=%s intent=%s target_url=%s", selected_types, intent, explicit_target_url)
        blog(f"Selected testing: {', '.join(selected_types)}")
        # execute_now (set when the user clicks "Run" in the UI) forces the full
        # auto flow — plan → generate → execute → coverage — with no approval gates.
        staged_unit = (
            "unit" in selected_types
            and not state.get("execute_now")
            and _wants_staged_unit_testing(user_prompt or "")
        )
        return {
            "classified_intent": intent,
            **_clear_previous_run_artifacts(),
            **contract,
            "staged_testing_enabled": staged_unit,
        }

    # Phase B.2 — pipeline-trigger fast path (precedes B.1 because the user
    # might say "run pipeline 42 on branch main" — we don't want to interpret
    # "branch main" as a clone request).
    pipeline_target = state.get("pipeline_target")
    if not pipeline_target and user_prompt:
        from agents_orchestrator.testing_agent.Nodes.pipeline import parse_pipeline_request
        parsed = parse_pipeline_request(user_prompt)
        if parsed:
            pipeline_target = parsed
    if isinstance(pipeline_target, dict) and pipeline_target.get("pipeline_id"):
        logger.info(f"Detected pipeline target {pipeline_target!r}, classifying as 'trigger_pipeline_and_collect'")
        blog(f"Detected pipeline trigger — pipeline {pipeline_target.get('pipeline_id')} on {pipeline_target.get('branch','main')}")
        return {"classified_intent": "trigger_pipeline_and_collect", "pipeline_target": pipeline_target}

    # Phase B.1 — clone-and-test fast path
    # 1. Structured clone_target form param already on state (from API layer)
    # 2. Or natural-language "test branch X of repo Y" pattern
    clone_target = state.get("clone_target")
    if not clone_target and user_prompt:
        from agents_orchestrator.testing_agent.tools.ado_clone import parse_clone_request
        parsed = parse_clone_request(user_prompt)
        if parsed:
            clone_target = parsed
    if clone_target and clone_target.get("repo") and clone_target.get("branch"):
        logger.info(f"Detected clone target {clone_target!r}, classifying as 'full_test' (B.1 path)")
        blog(f"Detected ADO clone target — branch {clone_target.get('branch')} of repo {clone_target.get('repo')}")
        return {"classified_intent": "full_test", "clone_target": clone_target}

    # Post-MVP Phase 6 — orchestrator-driven fast path. When invoked from the
    # orchestrator after dev finished (no upload, no explicit clone_target),
    # the user's affirmation ("yes", "test it", "approve", "go") would
    # otherwise route to the LLM classifier and usually come back as
    # "greeting" — so testing would do nothing despite having a real
    # development handoff to test. If upstream_development has repo_url +
    # branch_name AND no file is uploaded, route directly to full_test —
    # setup_workspace will pick up the upstream_dev.repo_url path naturally.
    upstream_dev = state.get("upstream_development") or {}
    if (
        not file_path
        and isinstance(upstream_dev, dict)
        and upstream_dev.get("repo_url")
        and upstream_dev.get("branch_name")
    ):
        # Ask the user what scope they want before routing.
        # Only ask once — if the user_prompt already answers the question
        # (contains "feature", "new", "changed", "full", "all", "entire"), use it.
        scope_prompt_lower = (user_prompt or "").lower()

        # â"€â"€ Code scope keywords â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€
        feature_keywords  = {"feature", "new", "changed", "diff", "pr", " a ", "option a", "choice a"}
        full_keywords     = {"full", "all", "entire", "everything", "whole", "complete", " b ", "option b", "choice b"}
        skip_code_keywords= {"skip code", "no code", "skip unit", "without code", "only api", "only ui",
                              " z ", "option z", "choice z", "no code test", "skip tests"}

        wants_feature   = any(k in scope_prompt_lower for k in feature_keywords)
        wants_full      = any(k in scope_prompt_lower for k in full_keywords)
        wants_skip_code = any(k in scope_prompt_lower for k in skip_code_keywords)

        # â"€â"€ API scope keywords â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€
        api_contract_keywords  = {" d ", "option d", "contract", "contract only", "contract test", "design contract"}
        api_functional_keywords= {" e ", "option e", "functional api", "api functional", "live api", "http test"}
        api_both_keywords      = {" f ", "option f", "both api", "contract and functional", "full api"}
        no_api_keywords        = {" c ", "option c", "no api", "skip api", "without api"}

        wants_api_contract  = any(k in scope_prompt_lower for k in api_contract_keywords)
        wants_api_functional= any(k in scope_prompt_lower for k in api_functional_keywords)
        wants_api_both      = any(k in scope_prompt_lower for k in api_both_keywords)

        if wants_api_both:
            api_scope: Optional[str] = "both"
        elif wants_api_functional:
            api_scope = "functional"
        elif wants_api_contract:
            api_scope = "contract_only"
        else:
            api_scope = None

        # â"€â"€ UI scope keywords â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€
        ui_feature_keywords = {" h ", "option h", "ui feature", "feature ui", "new feature ui", "ui for feature"}
        ui_full_keywords    = {" i ", "option i", "ui full", "full ui", "entire product", "whole product", "all pages"}

        wants_ui_feature = any(k in scope_prompt_lower for k in ui_feature_keywords)
        wants_ui_full    = any(k in scope_prompt_lower for k in ui_full_keywords)
        ui_scope: Optional[str] = "feature_only" if wants_ui_feature else ("full" if wants_ui_full else None)

        # â"€â"€ Nothing chosen yet — ask the question â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€
        if not wants_feature and not wants_full and not wants_skip_code and not api_scope and not ui_scope:
            message = _testing_scope_prompt(state, "Development is complete.")
            blog(message)
            return {"classified_intent": "greeting", "awaiting_scope": True, "final_user_message": message}
            branch = upstream_dev.get("branch_name", "the feature branch")
            blog(
                f"Development complete on branch **{branch}**. What testing would you like?\n\n"
                f"**Code Tests** *(pick one)*\n"
                f"  **A** — Feature only (changed files, fast)\n"
                f"  **B** — Full test suite (whole codebase)\n"
                f"  **Z** — Skip code tests\n\n"
                f"**API Tests** *(pick one)*\n"
                f"  **C** — No API testing\n"
                f"  **D** — Contract tests (validates code matches design specs — no server needed)\n"
                f"  **E** — Functional API tests (live HTTP against running service — needs URL)\n"
                f"  **F** — Both D + E\n\n"
                f"**UI / Browser Tests** *(pick one)*\n"
                f"  **G** — No UI testing\n"
                f"  **H** — UI: new feature only\n"
                f"  **I** — UI: entire product\n\n"
                f"Reply with your picks, e.g. **`A, C, G`** (code only) or **`Z, F, I, https://myapp.com`** (API + UI only)\n"
                f"URL required if you choose E, F, H, or I."
            )
            return {"classified_intent": "greeting", "awaiting_scope": True}

        # â"€â"€ Extract URL â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€
        combined_url = _extract_url(user_prompt or "")
        needs_url = (ui_scope is not None) or (api_scope in ("functional", "both"))

        if needs_url and not combined_url:
            blog(
                "URL required for functional API / UI tests but none provided — "
                "skipping those. Include `https://your-app.com` to enable them.",
                level="WARNING",
            )
            ui_scope = None
            api_scope = "contract_only" if api_scope in ("both", "functional") and api_scope == "both" else (
                None if api_scope == "functional" else api_scope
            )

        # â"€â"€ Decide code test scope â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€
        if wants_skip_code:
            test_scope: Optional[str] = None   # no code tests
        elif wants_feature:
            test_scope = "feature_only"
        elif wants_full:
            test_scope = "full"
        else:
            # Infer from context: if only API/UI requested, skip code tests
            test_scope = None if (api_scope or ui_scope) else "full"

        # â"€â"€ Route decision â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€
        # full_test    â†' code tests (+ optional API skills + optional UI after aggregate)
        # api_ui_only  â†' no code tests; runs only API skills + optional UI
        # ui_test      â†' UI only, no code, no API (existing lightweight path)
        if test_scope is not None:
            classified = "full_test"
        elif ui_scope and not api_scope:
            classified = "ui_test"          # existing path — no clone needed
        else:
            classified = "api_ui_only"      # clone for contract / skip clone for functional-only

        # â"€â"€ Summary â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€
        parts: list = []
        if test_scope:
            parts.append(f"code tests ({'feature only' if test_scope == 'feature_only' else 'full suite'})")
        if api_scope == "contract_only":
            parts.append("contract tests")
        elif api_scope == "functional":
            parts.append(f"functional API tests ({combined_url})")
        elif api_scope == "both":
            parts.append(f"contract + functional API tests ({combined_url})")
        if ui_scope:
            parts.append(f"UI tests ({'feature' if ui_scope == 'feature_only' else 'entire product'}) at {combined_url}")
        if not parts:
            parts = ["nothing — please re-run and make at least one choice"]

        blog("Running: " + " Â· ".join(parts))
        logger.info(
            "Upstream dev handoff — branch=%s classified=%s test_scope=%s api_scope=%s ui_scope=%s url=%s",
            upstream_dev.get("branch_name"), classified, test_scope, api_scope, ui_scope, combined_url,
        )

        result: dict = {
            "classified_intent": classified,
            "test_scope": test_scope,
            "api_scope": api_scope,
            "ui_scope": ui_scope,
            "awaiting_scope": False,
        }
        if combined_url:
            result["target_url"] = combined_url
        return result

    if file_path and file_path.endswith('.zip'):
        message = _testing_scope_prompt(state, "I received the code package.")
        blog(message)
        return {"classified_intent": "greeting", "awaiting_scope": True, "final_user_message": message}
        logger.info("Detected .zip file, classifying as 'full_test'.")
        blog("Detected ZIP file - proceeding with full test")
        return {"classified_intent": "full_test"}

    # Phase 2 + Phase M.5: bare single-file uploads (Python / .NET / React)
    # go through the full pipeline. The runner is picked by detect_language
    # based on file extension.
    if file_path and file_path.endswith(('.py', '.cs', '.jsx', '.tsx', '.js', '.ts')):
        message = _testing_scope_prompt(state, "I received the source file.")
        blog(message)
        return {"classified_intent": "greeting", "awaiting_scope": True, "final_user_message": message}
        ext = file_path.rsplit('.', 1)[-1]
        logger.info(f"Detected single .{ext} file, classifying as 'single_file_test'.")
        blog(f"Detected single .{ext} file - proceeding with full test+coverage")
        return {"classified_intent": "single_file_test"}

    target_url = _extract_url(user_prompt)
    if target_url:
        message = _testing_scope_prompt(state, "I received the URL.")
        blog(message)
        return {"classified_intent": "greeting", "awaiting_scope": True, "final_user_message": message, "target_url": target_url}
        logger.info(f"Detected URL '{target_url}', classifying as 'ui_test'.")
        blog(f"Detected URL - proceeding with UI test: {target_url}")
        return {"classified_intent": "ui_test", "target_url": target_url}

    # If the user mentions UI/functional/browser testing but gave no URL, ask for one
    # before routing — otherwise invoke_ui_testing_agent errors with target_url=None.
    ui_keywords = {"ui test", "functional test", "browser test", "selenium", "e2e",
                   "end to end", "end-to-end", "ui testing", "functional testing"}
    if user_prompt and any(kw in user_prompt.lower() for kw in ui_keywords):
        blog(
            "It looks like you want UI / functional testing. "
            "Please provide the application URL to test against "
            "(e.g. `https://your-app.azurewebsites.net`) and I'll run the tests.",
            level="INFO",
        )
        return {"classified_intent": "follow_up_query"}

    llm = get_llm()
    if history:
        logger.info("Conversation history detected. Performing advanced classification.")
        blog("Analyzing conversation history for context")
        history_str = "\n".join([f"{type(msg).__name__}: {msg.content}" for msg in history])
        prompt = (
            "Dispatcher: Is the user's latest message a follow-up question or a new task? "
            "Respond with 'follow_up_query' or 'new_task'.\n"
            f"HISTORY:\n{history_str}\n\nLATEST MESSAGE:\n{user_prompt}"
        )
        loop = asyncio.get_running_loop()
        response = await loop.run_in_executor(None, llm.invoke, prompt)
        record_response_usage(response)
        follow_up_classifier = response.content.strip().replace("'", "")

        logger.info(f"Advanced classifier result: '{follow_up_classifier}'")
        if follow_up_classifier == 'follow_up_query':
            blog("Handling follow-up query")
            return {"classified_intent": "follow_up_query"}

        logger.info("New task detected. Clearing previous analysis.")
        blog("New task detected - clearing previous state")
        state['test_plan'] = None
        state['requirement_analysis'] = None
        state['code_analysis'] = None
        state['ui_test_results'] = None

    logger.info("Performing standard intent classification.")
    blog("Performing standard intent classification")

    prompt = f"""
    You are an intelligent dispatcher agent. Classify the user's intent into one of two categories: 'generate_plan_only' or 'greeting'.

    1. **generate_plan_only**: User provides a text prompt, a document, or wants a test plan. Keywords: "write test cases", "create a test plan", "test this function".
    2. **greeting**: User is just saying hello, asking for help, asking what you can do, or the prompt is empty/non-specific. Keywords: "hi", "hello", "help", "what can you do".

    Analyze the user's prompt: "{user_prompt}"
    Respond with ONLY 'generate_plan_only' or 'greeting'.
    """
    loop = asyncio.get_running_loop()
    response = await loop.run_in_executor(None, llm.invoke, prompt)
    intent = response.content.strip().replace("'", "")

    logger.info(f"LLM classified intent as: '{intent}'")
    blog(f"Intent classified as: {intent}")
    return {"classified_intent": intent if intent in ["generate_plan_only", "greeting"] else "greeting"}


async def handle_greeting(state: SuperAgentState):
    logger.info("Handling greeting intent.")
    blog("Handling greeting intent")

    if state.get("final_user_message"):
        return {"final_user_message": state["final_user_message"]}

    # When invoked from the orchestrator (no upload, no clone target) and
    # we have NO upstream development context, the user almost certainly
    # meant to test what dev produced — ask for the repo/branch directly
    # instead of showing the generic capabilities list (which makes the
    # user think nothing happened and reply "yes test it" â†' triggers a
    # mis-route).
    is_orch_driven = (
        state.get("clone_target") is None
        and state.get("input_file_path") is None
    )
    upstream_dev = state.get("upstream_development") or {}
    has_upstream = (
        isinstance(upstream_dev, dict)
        and upstream_dev.get("repo_url")
        and upstream_dev.get("branch_name")
    )

    if is_orch_driven and not has_upstream:
        message = (
            "I'm ready to test, but I don't yet have the repo + branch the "
            "development agent built. This usually means the dev agent's "
            "handoff didn't reach me.\n\n"
            "Please tell me what to test in one of these forms:\n"
            "- `test branch <branch-name> of repo <repo-name> in project <project-name>`\n"
            "- `test repository <repo-name> in the project <project-name> at branch <branch-name>`\n"
            "- Or upload a `.zip` / single source file directly\n"
            "- Or paste a URL for UI testing"
        )
    else:
        message = (
            "Hello! I am a multi-talented testing agent. I can help you in a few ways:\n\n"
            "1.  **Generate a Test Plan:** Provide a user story or requirements, and I will create a test plan.\n"
            "2.  **Full Code Analysis & Testing:** Provide a `.zip` of a Python codebase for full analysis and unit testing.\n"
            "3.  **Automated UI Testing:** Provide a URL and I will autonomously generate and execute UI tests.\n\n"
            "How can I help you today?"
        )
    return {"final_user_message": message}


async def handle_follow_up_query(state: SuperAgentState):
    logger.info("Handling follow-up query.")
    blog("Processing follow-up query")

    user_prompt = state['user_prompt']
    history = state['chat_history']
    context_parts: list[str] = []
    if state.get('test_plan') and state['test_plan'].test_cases:
        context_parts.append(f"PREVIOUSLY GENERATED TEST PLAN:\n{state['test_plan'].model_dump_json(indent=2)}")
    if state.get('ui_test_results'):
        context_parts.append(f"PREVIOUS UI TEST RESULTS:\n{json.dumps(state['ui_test_results'], indent=2)}")
    if state.get('aggregated_results'):
        context_parts.append(f"LAST RUN RESULTS:\n{json.dumps(state['aggregated_results'], default=str)[:4000]}")
    context_str = "\n\n".join(context_parts) or "(no prior test run in this session yet)"

    # MCP-enabled chat: bind the project's assigned BYO MCP tools (loaded by the
    # API layer onto the mcp_runtime contextvar) so the QA assistant can call them
    # — same capability the other agents' chats have. Falls back to a plain answer
    # when no tools are present or the model can't tool-call.
    answer = await _answer_with_optional_mcp(user_prompt, history, context_str)

    new_history = list(history) + [HumanMessage(content=user_prompt), AIMessage(content=answer)]
    blog("Follow-up query answered")
    return {"final_user_message": answer, "chat_history": new_history}


async def _answer_with_optional_mcp(user_prompt: str, history, context_str: str) -> str:
    """Answer a QA chat turn, using BYO MCP tools when available (bounded loop)."""
    from langchain_core.messages import SystemMessage, ToolMessage

    try:
        from shared.tools.mcp_runtime import get_mcp_tools
        mcp_tools = list(get_mcp_tools() or [])
    except Exception:
        mcp_tools = []

    loop = asyncio.get_running_loop()
    sys = ("You are an expert QA assistant for an enterprise testing agent. Use the "
           "context and any available tools to answer the user's question precisely.\n"
           f"CONTEXT:\n{context_str}")

    if not mcp_tools:
        prompt = ChatPromptTemplate.from_messages([
            ("system", "{system}"), *history, ("human", "{user_prompt}"),
        ])
        chain = prompt | get_llm() | StrOutputParser()
        return await loop.run_in_executor(
            None, chain.invoke, {"system": sys, "user_prompt": user_prompt}
        )

    # Dedup tools by name (model APIs reject duplicate names) and run a bounded
    # tool-calling loop.
    seen: set = set()
    tools = []
    for t in mcp_tools:
        n = getattr(t, "name", None)
        if n and n not in seen:
            seen.add(n); tools.append(t)
    by_name = {getattr(t, "name", ""): t for t in tools}

    try:
        model = get_llm().bind_tools(tools)
    except Exception as exc:
        logger.info("Testing chat: model can't bind tools (%s) — plain answer", exc)
        prompt = ChatPromptTemplate.from_messages([
            ("system", "{system}"), *history, ("human", "{user_prompt}"),
        ])
        chain = prompt | get_llm() | StrOutputParser()
        return await loop.run_in_executor(
            None, chain.invoke, {"system": sys, "user_prompt": user_prompt}
        )

    messages = [SystemMessage(content=sys), *history, HumanMessage(content=user_prompt)]
    for _ in range(4):
        resp = await loop.run_in_executor(None, model.invoke, messages)
        messages.append(resp)
        tool_calls = getattr(resp, "tool_calls", None) or []
        if not tool_calls:
            content = resp.content
            if isinstance(content, list):
                content = "".join(b.get("text", "") for b in content if isinstance(b, dict))
            return content or "(no response)"
        for tc in tool_calls:
            tool = by_name.get(tc.get("name"))
            try:
                out = await tool.ainvoke(tc.get("args") or {}) if tool else f"unknown tool {tc.get('name')}"
            except Exception as exc:
                out = f"tool error: {exc}"
            messages.append(ToolMessage(content=str(out)[:8000], tool_call_id=tc.get("id", "")))
    return "I wasn't able to finish using the tools for that — please rephrase."


async def read_input_content(state: SuperAgentState):
    logger.info("Reading input file content...")
    blog("Reading input file content...")

    file_path = state.get("input_file_path")
    prompt_text = state.get("user_prompt", "")
    file_content = ""

    if file_path and os.path.exists(file_path):
        try:
            async with aiofiles.open(file_path, 'r', encoding='utf-8') as f:
                file_content = await f.read()
            logger.info(f"Successfully read content from {file_path}")
            blog(f"Successfully read file: {os.path.basename(file_path)}")
        except Exception as exc:
            logger.error(f"Error reading file {file_path}: {exc}")
            blog(f"Error reading file: {exc}", level="ERROR")

    full_content = f"User Prompt: {prompt_text}\n\n--- Document Content ---\n{file_content}".strip()
    return {"input_content": full_content}
