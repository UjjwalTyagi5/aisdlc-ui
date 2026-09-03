"""Testing-agent LangGraph compile + entrypoints.

Phase 4a — extracted from `super_agent.py` lines 1000-1282 with no behavioural change.
This module:
- registers every Node from the new `Nodes/` package
- wires them into the StateGraph exactly as the monolithic super_agent did
- exposes `app` (compiled graph) and the three `run_super_agent[*]` entrypoints
  that `testing_agent_api.py` and any external import sites depend on.
"""
from __future__ import annotations

import asyncio
import contextvars
import logging
from typing import Dict, Optional

from langgraph.graph import END, StateGraph
from langgraph.utils.runnable import RunnableCallable

from agents_orchestrator.testing_agent.config.session_state import SuperAgentState
from agents_orchestrator.testing_agent.config.shared import (
    BROADCASTING_AVAILABLE,
    blog,
    logger,
    manager,
    set_session_id,
)

# Node functions (async)
from agents_orchestrator.testing_agent.Nodes.ingest_input import (
    classify_intent,
    handle_follow_up_query,
    handle_greeting,
    pull_upstream_context,
    read_input_content,
)
from agents_orchestrator.testing_agent.Nodes.workspace import (
    setup_single_file_workspace,
    setup_workspace,
)
from agents_orchestrator.testing_agent.Nodes.detect_language import detect_language
from agents_orchestrator.testing_agent.Nodes.pipeline import trigger_and_poll
from agents_orchestrator.testing_agent.Nodes.analyze import (
    analyze_requirements,
    find_and_analyze_code,
    handle_analysis_failure,
)
from agents_orchestrator.testing_agent.Nodes.plan import generate_test_plan
from agents_orchestrator.testing_agent.Nodes.generate import (
    generate_test_code,
    save_generated_code,
)
from agents_orchestrator.testing_agent.Nodes.execute import (
    handle_ui_test_failure,
    invoke_ui_testing_agent,
    run_code_testing_agent,
    summarize_ui_test_results,
)
from agents_orchestrator.testing_agent.Nodes.finalize import (
    cleanup_workspace,
    generate_final_message,
    package_final_reports,
)
from agents_orchestrator.testing_agent.Nodes.routing import (
    decide_after_aggregate,
    decide_after_plan_generation,
    decide_after_test_code_saved,
    decide_cleanup,
    decide_code_analysis_outcome,
    decide_ui_test_outcome,
    make_sync_wrapper,
    route_by_intent,
)
from agents_orchestrator.testing_agent.Nodes.dispatch_test_types import dispatch_test_types
from agents_orchestrator.testing_agent.Nodes.approval import (
    generate_ui_test_plan_and_request_approval,
    request_functional_execution_approval,
    request_unit_execution_approval,
    request_unit_generation_approval,
)
from agents_orchestrator.testing_agent.Nodes.aggregate_test_results import aggregate_test_results
from agents_orchestrator.testing_agent.Nodes.generate_qa_report import generate_qa_report


# --- Wrap async nodes for sync LangGraph invoke ----------------------------
# `pull_upstream_context` needs BOTH a sync AND an async entrypoint:
#   - The standalone testing agent drives the compiled graph via SYNC
#     `app.invoke(...)` (`run_super_agent` and `run_super_agent_async`'s
#     `loop.run_in_executor(None, lambda: ctx.run(app.invoke, initial_state))`).
#     LangGraph's sync `.invoke()` cannot execute an async-only node — it
#     raises `TypeError: No synchronous function provided to
#     "pull_upstream_context"` the moment it hits an async `func=None` node.
#   - The Copilot drives the SAME compiled builder via `await
#     graph.ainvoke(...)` on the main loop. `pull_upstream_context` awaits
#     `fetch_session_artifacts`, which touches the shared asyncpg pool bound
#     to whatever loop is driving it. Wrapping it with `make_sync_wrapper`
#     (which spins a brand-new loop via `asyncio.run` in a worker thread)
#     would raise `RuntimeError: got Future attached to a different loop`
#     under `ainvoke` because the pool's Futures are attached to the main
#     loop, not the worker's throwaway loop.
# `RunnableCallable(func, afunc, ...)` lets ONE node carry both: LangGraph's
# sync `.invoke()` calls `func` (the sync wrapper); its async `.ainvoke()`
# calls `afunc` (the native coroutine) directly on the driving loop. See
# `tests/copilot/test_testing_upstream_loop.py` for the regression coverage.
pull_upstream_context_sync = make_sync_wrapper(pull_upstream_context)
classify_intent_sync = make_sync_wrapper(classify_intent)
handle_greeting_sync = make_sync_wrapper(handle_greeting)
handle_follow_up_query_sync = make_sync_wrapper(handle_follow_up_query)
read_input_content_sync = make_sync_wrapper(read_input_content)
analyze_requirements_sync = make_sync_wrapper(analyze_requirements)
setup_workspace_sync = make_sync_wrapper(setup_workspace)
setup_single_file_workspace_sync = make_sync_wrapper(setup_single_file_workspace)
detect_language_sync = make_sync_wrapper(detect_language)
trigger_and_poll_sync = make_sync_wrapper(trigger_and_poll)
find_and_analyze_code_sync = make_sync_wrapper(find_and_analyze_code)
handle_analysis_failure_sync = make_sync_wrapper(handle_analysis_failure)
generate_test_plan_sync = make_sync_wrapper(generate_test_plan)
generate_test_code_sync = make_sync_wrapper(generate_test_code)
save_generated_code_sync = make_sync_wrapper(save_generated_code)
run_code_testing_agent_sync = make_sync_wrapper(run_code_testing_agent)
invoke_ui_testing_agent_sync = make_sync_wrapper(invoke_ui_testing_agent)
handle_ui_test_failure_sync = make_sync_wrapper(handle_ui_test_failure)
summarize_ui_test_results_sync = make_sync_wrapper(summarize_ui_test_results)
generate_final_message_sync = make_sync_wrapper(generate_final_message)
package_final_reports_sync = make_sync_wrapper(package_final_reports)
cleanup_workspace_sync = make_sync_wrapper(cleanup_workspace)
dispatch_test_types_sync = make_sync_wrapper(dispatch_test_types)
request_unit_generation_approval_sync = make_sync_wrapper(request_unit_generation_approval)
request_unit_execution_approval_sync = make_sync_wrapper(request_unit_execution_approval)
request_functional_execution_approval_sync = make_sync_wrapper(request_functional_execution_approval)
generate_ui_test_plan_and_request_approval_sync = make_sync_wrapper(generate_ui_test_plan_and_request_approval)
aggregate_test_results_sync = make_sync_wrapper(aggregate_test_results)
generate_qa_report_sync = make_sync_wrapper(generate_qa_report)


# --- Graph wiring (mirrors `super_agent.py:1039-1131`) ---------------------
graph_builder = StateGraph(SuperAgentState)

graph_builder.add_node(
    "pull_upstream_context",
    RunnableCallable(pull_upstream_context_sync, pull_upstream_context, name="pull_upstream_context"),
)
graph_builder.add_node("classify_intent", classify_intent_sync)
graph_builder.add_node("handle_greeting", handle_greeting_sync)
graph_builder.add_node("handle_follow_up_query", handle_follow_up_query_sync)
graph_builder.add_node("read_input_content", read_input_content_sync)
graph_builder.add_node("analyze_requirements", analyze_requirements_sync)
graph_builder.add_node("generate_plan_from_reqs", generate_test_plan_sync)
graph_builder.add_node("generate_plan_summary", generate_final_message_sync)
graph_builder.add_node("setup_workspace", setup_workspace_sync)
# api_ui_only path reuses the same clone logic but bypasses code analysis
graph_builder.add_node("setup_workspace_api_ui", setup_workspace_sync)
graph_builder.add_node("detect_language_api_ui", detect_language_sync)
graph_builder.add_node("dispatch_test_types_api_ui", dispatch_test_types_sync)
graph_builder.add_node("setup_single_file_workspace", setup_single_file_workspace_sync)
graph_builder.add_node("detect_language", detect_language_sync)
graph_builder.add_node("trigger_and_poll", trigger_and_poll_sync)
graph_builder.add_node("find_and_analyze_code", find_and_analyze_code_sync)
graph_builder.add_node("handle_analysis_failure", handle_analysis_failure_sync)
graph_builder.add_node("generate_plan_from_code", generate_test_plan_sync)
graph_builder.add_node("generate_test_code", generate_test_code_sync)
graph_builder.add_node("save_generated_code", save_generated_code_sync)
graph_builder.add_node("dispatch_test_types", dispatch_test_types_sync)
graph_builder.add_node("request_unit_generation_approval", request_unit_generation_approval_sync)
graph_builder.add_node("request_unit_execution_approval", request_unit_execution_approval_sync)
graph_builder.add_node("request_functional_execution_approval", request_functional_execution_approval_sync)
graph_builder.add_node("generate_ui_test_plan_and_request_approval", generate_ui_test_plan_and_request_approval_sync)
graph_builder.add_node("aggregate_test_results", aggregate_test_results_sync)
graph_builder.add_node("generate_qa_report", generate_qa_report_sync)
graph_builder.add_node("run_code_testing_agent", run_code_testing_agent_sync)
graph_builder.add_node("generate_execution_summary", generate_final_message_sync)
graph_builder.add_node("invoke_ui_testing_agent", invoke_ui_testing_agent_sync)
graph_builder.add_node("summarize_ui_test_results", summarize_ui_test_results_sync)
graph_builder.add_node("handle_ui_test_failure", handle_ui_test_failure_sync)
graph_builder.add_node("package_final_reports", package_final_reports_sync)
graph_builder.add_node("cleanup_workspace", cleanup_workspace_sync)

# Phase 5 — pull_upstream_context runs first so analyze_requirements can short-circuit
# from upstream stories and (Phase B.1) workspace can clone from upstream repo_url.
graph_builder.set_entry_point("pull_upstream_context")
graph_builder.add_edge("pull_upstream_context", "classify_intent")
graph_builder.add_conditional_edges("classify_intent", route_by_intent, {
    "approve_generate_unit_code_path": "dispatch_test_types",
    "approve_run_unit_tests_path": "run_code_testing_agent",
    # Functional resume: generated tests already in state — run them directly
    "approve_run_functional_tests_path": "run_code_testing_agent",
    # UI resume: test plan already shown — go straight to Selenium agent
    "approve_run_ui_tests_path": "invoke_ui_testing_agent",
    "full_test_path": "setup_workspace",
    "single_file_test_path": "setup_single_file_workspace",
    "generate_plan_only_path": "read_input_content",
    # ui_test now shows a plan first; Selenium runs only after approval
    "ui_test_path": "generate_ui_test_plan_and_request_approval",
    # api_ui_only: no code tests — clone repo (for contract), run API skills, optional UI
    # Reuses setup_workspace for the clone, but jumps directly to dispatch after detect_language,
    # bypassing find_and_analyze_code / generate_test_plan entirely via a dedicated edge below.
    "api_ui_only_path": "setup_workspace_api_ui",
    "greeting_path": "handle_greeting",
    "follow_up_path": "handle_follow_up_query",
    "trigger_pipeline_path": "trigger_and_poll",
})

# Phase B.2 — pipeline trigger path joins the common exit (package_final_reports)
graph_builder.add_edge("trigger_and_poll", "package_final_reports")

# Phase 2: single-.py path joins the existing analyze→plan→generate→run pipeline.
# Phase M.5: detect_language is inserted between workspace setup and find_and_analyze_code
# so downstream nodes can dispatch through state["runner"] (Python / .NET / React).
graph_builder.add_edge("setup_single_file_workspace", "detect_language")
graph_builder.add_edge("detect_language", "find_and_analyze_code")

# Trivial-exit branches
graph_builder.add_edge("handle_greeting", "package_final_reports")
graph_builder.add_edge("handle_follow_up_query", "package_final_reports")
graph_builder.add_edge("handle_ui_test_failure", "package_final_reports")
graph_builder.add_edge("handle_analysis_failure", "package_final_reports")

# Plan-from-text path
graph_builder.add_edge("read_input_content", "analyze_requirements")
graph_builder.add_edge("analyze_requirements", "generate_plan_from_reqs")
graph_builder.add_edge("generate_plan_from_reqs", "generate_plan_summary")
graph_builder.add_edge("generate_plan_summary", "package_final_reports")

# Full-code test path — also passes through detect_language (Phase M.5)
graph_builder.add_edge("setup_workspace", "detect_language")

# api_ui_only path: clone → detect language → dispatch (API skills only) → aggregate → optional UI
# Bypasses find_and_analyze_code, generate_plan, run_code_testing_agent entirely.
graph_builder.add_edge("setup_workspace_api_ui", "detect_language_api_ui")
graph_builder.add_edge("detect_language_api_ui", "dispatch_test_types_api_ui")
graph_builder.add_edge("dispatch_test_types_api_ui", "save_generated_code")
graph_builder.add_conditional_edges(
    "find_and_analyze_code",
    decide_code_analysis_outcome,
    {"analysis_succeeded": "generate_plan_from_code", "analysis_failed": "handle_analysis_failure"},
)
# Phase 10 — fan-out replaces direct generate_test_code edge.
graph_builder.add_conditional_edges(
    "generate_plan_from_code",
    decide_after_plan_generation,
    {"await_generate_unit_approval": "request_unit_generation_approval", "dispatch": "dispatch_test_types"},
)
graph_builder.add_edge("request_unit_generation_approval", "package_final_reports")
graph_builder.add_edge("dispatch_test_types", "save_generated_code")
graph_builder.add_edge("generate_test_code", "save_generated_code")
graph_builder.add_conditional_edges(
    "save_generated_code",
    decide_after_test_code_saved,
    {
        "await_run_approval": "request_unit_execution_approval",
        "await_functional_approval": "request_functional_execution_approval",
        "run": "run_code_testing_agent",
    },
)
graph_builder.add_edge("request_unit_execution_approval", "package_final_reports")
# Functional approval gate: show generated test plan, wait for user reply
graph_builder.add_edge("request_functional_execution_approval", "package_final_reports")
# UI test plan + approval gate: show LLM-drafted plan, wait for user reply
graph_builder.add_edge("generate_ui_test_plan_and_request_approval", "package_final_reports")
# Phase 10 — splice aggregate + qa_report between exec and summary.
graph_builder.add_edge("run_code_testing_agent", "aggregate_test_results")
# After aggregating code results: run UI tests if ui_scope+target_url are set
# and UI hasn't run yet; otherwise go straight to QA report.
graph_builder.add_conditional_edges(
    "aggregate_test_results",
    decide_after_aggregate,
    {"run_ui_tests": "invoke_ui_testing_agent", "generate_report": "generate_qa_report"},
)
graph_builder.add_edge("generate_qa_report", "generate_execution_summary")
graph_builder.add_edge("generate_execution_summary", "package_final_reports")

# UI test path
graph_builder.add_conditional_edges(
    "invoke_ui_testing_agent",
    decide_ui_test_outcome,
    {"success": "summarize_ui_test_results", "failure": "handle_ui_test_failure"},
)
# After UI tests: mark complete (prevents re-entry), merge results, then QA report.
graph_builder.add_edge("summarize_ui_test_results", "aggregate_test_results")

# Common exit
graph_builder.add_conditional_edges(
    "package_final_reports",
    decide_cleanup,
    {"needs_cleanup": "cleanup_workspace", "no_cleanup_needed": END},
)
graph_builder.add_edge("cleanup_workspace", END)

app = graph_builder.compile()
super_agent_graph = app  # alias kept for external imports


# --- 6. Main Execution Functions (kept verbatim — public API) --------------
def _initial_state(user_prompt, input_file_path, previous_state,
                   tenant_id=None, model_id=None, offering_id=None):
    if previous_state:
        logger.info("Continuing from a previous state.")
        initial = {key: val for key, val in previous_state.items()}
        resumed_input_file_path = input_file_path or previous_state.get("input_file_path")
        initial.update({
            "user_prompt": user_prompt,
            "input_file_path": resumed_input_file_path,
            "classified_intent": "unsupported",
            "final_user_message": None,
            "final_outputs": {},
            "error_message": None,
        })
        # BYOK (P3.6): carry forward tenant/model context. Explicit args win;
        # otherwise preserve whatever the previous state already held.
        if tenant_id is not None or "tenant_id" not in initial:
            initial["tenant_id"] = tenant_id if tenant_id is not None else initial.get("tenant_id")
        if model_id is not None or "model_id" not in initial:
            initial["model_id"] = model_id if model_id is not None else initial.get("model_id")
        if offering_id is not None or "offering_id" not in initial:
            initial["offering_id"] = offering_id if offering_id is not None else initial.get("offering_id")
        return initial
    logger.info("Starting a new state.")
    return {
        "user_prompt": user_prompt, "input_file_path": input_file_path,
        "classified_intent": "unsupported", "input_content": None, "work_dir": None,
        "code_analysis": None, "requirement_analysis": None, "test_plan": None,
        "generated_test_code": None, "test_execution_summary": None,
        "final_user_message": None, "final_outputs": {}, "chat_history": [],
        "target_url": None, "ui_test_results": None, "error_message": None,
        "tenant_id": tenant_id, "model_id": model_id, "offering_id": offering_id,
    }


async def _resolve_and_stash_model(tenant_id: Optional[str], model_id: Optional[str],
                                   offering_id: Optional[str] = None,
                                   project_id: Optional[str] = None):
    """Resolve the run's BYOK model and stash it in the model_resolver contextvar
    BEFORE the graph runs in the executor. The whole testing graph is driven
    synchronously via `app.invoke` inside `loop.run_in_executor`, and each node
    re-enters asyncio via `asyncio.run` — a plain contextvar set inside a node
    would not survive across nodes. Setting it here + running `app.invoke` under
    `copy_context().run` makes the resolved model visible to every node/tool LLM
    site for the lifetime of this run. Fail-closed: re-raises on resolver errors.
    """
    from shared.services.model_resolver import (
        NoModelConfiguredError, ModelNotEnabledError, ResolvedModel,
        resolve_model_for_run, set_resolved_model,
    )
    try:
        # WITHOUT project_id THIS FAILS CLOSED ON EVERY GOVERNED TENANT. Once an org
        # has a single org_model_grants row, effective_project_offerings needs the
        # project to know which offerings apply; with no project it matches none and
        # raises NoModelConfiguredError — so a correctly-configured org gets told it
        # has no model, and the except-branch below quietly runs the whole testing
        # run on a local .env key instead of the org's verified BYOK provider. The
        # deployment and code-review agents already pass it.
        resolved = await resolve_model_for_run(
            tenant_id or "", model_id, offering_id=offering_id, project_id=project_id,
        )
    except (NoModelConfiguredError, ModelNotEnabledError):
        # Local-dev fallback: in non-enterprise mode the model key lives in .env
        # (no DB-configured BYOK provider). Mirror every other agent's behaviour
        # and resolve from ANTHROPIC_API_KEY. Enterprise mode still fails closed.
        from config.env import AGENT_RUNTIME_MODE, ANTHROPIC_API_KEY, ANTHROPIC_MODEL
        if AGENT_RUNTIME_MODE == "enterprise" or not ANTHROPIC_API_KEY:
            raise
        resolved = ResolvedModel(
            provider="anthropic",
            litellm_provider="anthropic",
            model=model_id or ANTHROPIC_MODEL,
            api_key=ANTHROPIC_API_KEY,
            base_url=None,
            alias="local-env:anthropic",
            display_name="Local .env (Anthropic)",
        )
    set_resolved_model(resolved)
    return resolved


async def run_super_agent_async(
    user_prompt: Optional[str],
    input_file_path: Optional[str],
    previous_state: Optional[Dict] = None,
    session_id: Optional[str] = None,
    tenant_id: Optional[str] = None,
    model_id: Optional[str] = None,
    offering_id: Optional[str] = None,
    callbacks: Optional[list] = None,
) -> Dict:
    """Async runner with broadcasting support.

    BYOK (P3.6 B2): resolves the tenant's BYOK model up front and stashes it in
    the model_resolver contextvar, then runs the graph under a copied context so
    every node/tool LLM site builds its client from the resolved model. Fails
    CLOSED with a clear final message when no model is configured/enabled.
    """
    logger.info(f"--- Starting async agent run for prompt: '{user_prompt}', file: '{input_file_path}' ---")

    if BROADCASTING_AVAILABLE and session_id:
        set_session_id(session_id)

    blog("Starting super agent execution")

    # Carry tenant/model from a resumed state when the caller didn't pass them.
    if previous_state:
        tenant_id = tenant_id if tenant_id is not None else previous_state.get("tenant_id")
        model_id = model_id if model_id is not None else previous_state.get("model_id")
        offering_id = offering_id if offering_id is not None else previous_state.get("offering_id")
    _project_id = (previous_state or {}).get("project_id")

    # Resolve + stash the BYOK model before entering the executor. Fail closed.
    from shared.services.model_resolver import NoModelConfiguredError, ModelNotEnabledError
    try:
        resolved = await _resolve_and_stash_model(tenant_id, model_id, offering_id,
                                                  project_id=_project_id)
        blog(f"Using model {resolved.alias} for testing run")
    except (NoModelConfiguredError, ModelNotEnabledError) as exc:
        msg = (
            "No usable model is configured for your organization. An administrator "
            "must add and verify a model provider in Org Settings → Model Providers."
        )
        logger.warning("Testing model resolution failed (tenant=%s): %s",
                       tenant_id, type(exc).__name__)
        blog(msg, level="ERROR")
        initial_state = _initial_state(user_prompt, input_file_path, previous_state,
                                       tenant_id=tenant_id, model_id=model_id, offering_id=offering_id)
        initial_state["final_user_message"] = msg
        initial_state["error_message"] = msg
        return initial_state

    initial_state = _initial_state(user_prompt, input_file_path, previous_state,
                                   tenant_id=tenant_id, model_id=model_id, offering_id=offering_id)

    try:
        loop = asyncio.get_running_loop()
        # The resolved-model contextvar is set on THIS context; run app.invoke
        # under a copy so the executor thread + each node's asyncio.run inherit it.
        ctx = contextvars.copy_context()
        # Thread per-run callbacks (audit/cost + Langfuse) into the graph. The copied
        # context carries the active Langfuse OTEL span so generations nest under the
        # run's testing span; callbacks propagate to each node via LangGraph config.
        _invoke_cfg = {"callbacks": callbacks} if callbacks else None
        final_state = await loop.run_in_executor(
            None, lambda: ctx.run(app.invoke, initial_state, _invoke_cfg)
        )
        blog("Super agent execution completed successfully")
        logger.info("--- Async agent run complete. ---")
        return final_state
    except Exception as exc:
        error_msg = f"Super agent execution failed: {exc}"
        logger.error(error_msg)
        blog(error_msg, level="ERROR")
        raise


def run_super_agent(
    user_prompt: Optional[str],
    input_file_path: Optional[str],
    previous_state: Optional[Dict] = None,
) -> Dict:
    """Synchronous wrapper for backward compatibility (REST handler path)."""
    logger.info(f"--- Starting agent run for prompt: '{user_prompt}', file: '{input_file_path}' ---")
    initial_state = _initial_state(user_prompt, input_file_path, previous_state)
    final_state = app.invoke(initial_state)
    logger.info("--- Agent run complete. ---")
    return final_state


async def run_super_agent_with_streaming(
    user_prompt: Optional[str],
    input_file_path: Optional[str],
    previous_state: Optional[Dict] = None,
    session_id: Optional[str] = None,
    progress_callback=None,
) -> Dict:
    """Stream the LangGraph execution events. Each yielded event is broadcast as
    a `progress` WS message and forwarded to `progress_callback` if supplied."""
    if BROADCASTING_AVAILABLE and session_id:
        set_session_id(session_id)

    logger.info("Starting super agent with streaming...")
    blog("Initializing streaming execution")
    initial_state = _initial_state(user_prompt, input_file_path, previous_state)

    try:
        final_state = None
        async for event in app.astream(initial_state):
            if progress_callback:
                await progress_callback(event)
            if BROADCASTING_AVAILABLE:
                node_name = list(event.keys())[0] if event else "unknown"
                await manager.broadcast({
                    "type": "progress",
                    "session_id": session_id or "default",
                    "node": node_name,
                    "message": f"Executing: {node_name}",
                })
            final_state = event
        blog("Streaming execution completed")
        return final_state
    except Exception as exc:
        error_msg = f"Streaming execution failed: {exc}"
        logger.error(error_msg)
        blog(error_msg, level="ERROR")
        raise
