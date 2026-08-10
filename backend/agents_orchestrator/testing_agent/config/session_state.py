"""Pydantic models + LangGraph state for the testing agent.

Phase 4a — extracted from `super_agent.py` lines 93-141 with no behavioural change.
Future phases (5/M) extend `SuperAgentState` with `upstream_*`, `repo_workspace`,
`clone_target`, `language`, `runner`, etc.
"""
from __future__ import annotations

from typing import Dict, List, Literal, Optional, TypedDict

from pydantic import BaseModel, Field


class FunctionInfo(BaseModel):
    function_name: str = Field(..., description="The name of the function.")
    file_path: str = Field(..., description="The file path where the function is located.")
    summary: str = Field(..., description="A short summary of what the function does.")
    input_format: str = Field(..., description="Description of expected input parameters, their names, and types.")
    output_format: str = Field(..., description="Description of the expected output or return value and its type.")


class CodeAnalysis(BaseModel):
    functions: List[FunctionInfo]


class RequirementInfo(BaseModel):
    requirement_id: str = Field(..., description="A unique identifier for the requirement (e.g., 'REQ-01').")
    description: str = Field(..., description="A clear, concise description of the user story or requirement.")
    acceptance_criteria: List[str] = Field(..., description="A list of conditions that must be met for the requirement to be considered complete.")


class RequirementAnalysis(BaseModel):
    requirements: List[RequirementInfo]


class TestCase(BaseModel):
    test_case_id: str = Field(..., description="Unique ID for the test case (e.g., 'TC-REQ-01').")
    feature_or_function_tested: str = Field(..., description="The name of the function or requirement this test case is for.")
    test_summary: str = Field(..., description="A short, descriptive title for the test case.")
    scenario_type: str = Field(..., description="Type of test: 'Happy Path', 'Error Case', or 'Edge Case'.")
    test_steps: str = Field(..., description="Step-by-step actions to perform, formatted as a numbered list.")
    test_data: str = Field(..., description="Specific input data to be used for the test.")
    expected_result: str = Field(..., description="The expected outcome or error after executing the test.")


class TestPlan(BaseModel):
    test_cases: List[TestCase]


class SuperAgentState(TypedDict, total=False):
    # BYOK (P3.6) — tenant + requested model threaded from the Temporal activity
    # so the run resolves the org's BYOK model (no platform-key fallback).
    tenant_id: Optional[str]
    model_id: Optional[str]
    offering_id: Optional[str]
    user_prompt: Optional[str]
    input_file_path: Optional[str]
    classified_intent: Literal[
        "full_test", "single_file_test", "generate_plan_only",
        "ui_test", "greeting", "follow_up_query", "unsupported",
        "trigger_pipeline_and_collect", "api_ui_only",
    ]
    input_content: Optional[str]
    work_dir: Optional[str]
    code_analysis: Optional[CodeAnalysis]
    requirement_analysis: Optional[RequirementAnalysis]
    test_plan: Optional[TestPlan]
    generated_test_code: Optional[str]
    test_execution_summary: Optional[str]
    test_run_attempted: Optional[bool]
    test_runner_exit_code: Optional[int]
    test_runner_stdout: Optional[str]
    test_runner_stderr: Optional[str]
    final_user_message: Optional[str]
    final_outputs: Dict[str, str]
    chat_history: List[tuple]
    target_url: Optional[str]
    ui_test_results: Optional[List[Dict]]
    error_message: Optional[str]
    # Phase 5 — upstream-context fields populated by `pull_upstream_context` node
    upstream_requirements: Optional[Dict]   # raw requirements_payload dict
    upstream_development: Optional[Dict]    # raw development_artifacts dict
    upstream_design: Optional[Dict]         # raw design_artifacts dict
    upstream_context_str: Optional[str]     # formatted block from context_broker
    upstream_loaded: bool                   # idempotency guard for pop_cached_context
    repo_workspace: Optional[str]           # work_dir filled by ADO clone (Phase B.1)
    clone_target: Optional[Dict]            # {project, repo, branch} from chat or form param (Phase B.1)
    test_scope: Optional[str]              # "feature_only" | "full" — code test scope
    ui_scope: Optional[str]               # "feature_only" | "full" | None — UI browser test scope
    api_scope: Optional[str]              # "contract_only" | "functional" | "both" | None — API test scope
    selected_test_types: Optional[List[str]]  # ["unit", "functional", "api"] from direct UI / orchestrator gate
    api_timeout_s: Optional[float]         # per-request timeout for generated API tests
    awaiting_scope: Optional[bool]         # True while waiting for user to answer the scope question
    pending_testing_approval: Optional[str]  # "generate_unit_code" | "run_unit_tests" while staged testing waits for user approval
    staged_testing_enabled: Optional[bool]   # True when unit testing should pause between plan, generation, and execution
    execute_now: Optional[bool]              # UI "Run" button — force full auto flow (no staged-approval gates)
    ui_tests_completed: Optional[bool]    # guard to prevent aggregate→ui→aggregate infinite loop
    # Phase M.5 — language detection results
    language: Optional[str]                 # "python" | "dotnet" | "react" | "unknown"
    runner_command: Optional[str]           # the exact CLI the chosen runner emitted (for replay)
    # Phase B.2 — ADO pipeline trigger
    pipeline_target: Optional[Dict]         # {project, pipeline_id, branch}
    pipeline_run: Optional[Dict]            # PipelineRun.model_dump() after polling completes
    # Phase B.3 — Trivy/Sonar parsed findings (List[Dict])
    security_findings: Optional[List[Dict]]
    # QoL — coverage iteration counter + threshold (default 80% applied at runtime)
    iter_count: int
    coverage_threshold: float
    # Post-MVP Phase 1 — graceful ADO-clone failure propagation. When set, the
    # graph routes through handle_analysis_failure → package_final_reports with
    # this string as the user-facing message instead of raising 500.
    clone_error_message: Optional[str]
    # Post-MVP Phase 5 — when True, the workspace was reused from the dev
    # agent's local clone instead of a fresh ADO remote clone. Telemetry only.
    reused_dev_workspace: bool
    # Post-MVP Phase 7 — set by setup_workspace when we cloned a PR branch.
    # Used by analyze + execute to compute PR-scoped diff (smaller LLM
    # prompt → no empty TestPlans on big repos; PR-scoped coverage % shown
    # in chat alongside whole-repo %).
    pr_branch: Optional[str]
    # PR-scoped coverage result computed by run_code_testing_agent.
    # PRCoverageSummary.model_dump() — kept as dict to keep state JSON-serializable
    # for langgraph checkpointing.
    pr_coverage: Optional[Dict]
    # Phase 8.4 — output_dir set by package_final_reports so _build_testing_artifact
    # can rewrite XML path fields to durable copies (work_dir gets deleted by
    # cleanup_workspace; original paths in TestingArtifact would otherwise be stale).
    output_dir: Optional[str]
    # Phase 8.9d — orchestrator's full prior message list (JSON-decoded list of
    # {role, content} dicts), passed through by the API layer's chat handlers
    # when called from the orchestrator. Used by pull_upstream_context to mine
    # dev-agent chat output for branch/repo/project hints when the
    # development_artifacts row is NULL (broken upstream Django persistence).
    orchestrator_chat_history: Optional[List[Dict]]
    # Phase 10 — fan-out test-type generation (Skill-packaged generators).
    # Each entry produced by dispatch_test_types; consumed by aggregate_test_results.
    generated_test_sets: Optional[List[Dict]]
    """Per-skill: {skill_name, test_file_path, framework, scenario_count}."""

    skill_failures: Optional[List[str]]
    """Human-readable failure messages for skills whose generation raised."""

    # Phase 10 — aggregator output. Pydantic AggregatedResults.model_dump().
    aggregated_results: Optional[Dict]

    # Phase 10 — defect log accumulated across all test types.
    defect_log: Optional[List[Dict]]

    # Phase 10 — QA report paths (durable, point at output_dir not work_dir).
    qa_report_html_path: Optional[str]
    qa_report_pdf_path: Optional[str]

    # Functional test approval gate — always active for functional/UI testing paths.
    # "run_functional_tests" | "run_ui_tests" while waiting for user approval.
    pending_functional_approval: Optional[str]
    # Set to True once the user approves; clears the gate for resume runs.
    functional_tests_approved: Optional[bool]
    # Path to the human-readable markdown test plan file written before asking approval.
    functional_test_plan_file: Optional[str]
    # JSON string — list of {id, description, goal} dicts generated at approval time.
    # Passed to the UI testing agent so it executes exactly the approved test cases.
    ui_test_plan_json: Optional[str]
