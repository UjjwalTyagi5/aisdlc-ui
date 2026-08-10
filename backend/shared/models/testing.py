"""TestingArtifact + supporting models — produced by the testing agent.

The deployment agent (and orchestrator) consumes this via
agent_session.testing_artifacts after the testing agent has run.

Mirror of shared/models/development.py's pattern.

Phase M.6 added: language, runner_command (multi-language support).
Phase B.2 + B.3 pre-added: pipeline_run, security_findings (so the schema
doesn't need a second bump when those phases land).
"""
from __future__ import annotations

from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class CoverageSummary(BaseModel):
    """Cobertura-style coverage roll-up parsed from coverage.xml."""

    statements: int = 0                              # total executable lines
    missed: int = 0                                  # lines not executed
    coverage_pct: float = 0.0                        # 0..100
    branch_coverage_pct: Optional[float] = None      # may be None if branch coverage isn't tracked
    coverage_xml_path: Optional[str] = None          # path to the raw cobertura xml on disk


class TestExecution(BaseModel):
    """Test-run result parsed from results.xml (JUnit format)."""

    framework: Literal["pytest", "xunit", "jest", "ui_browser"] = "pytest"
    total: int = 0
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    errors: int = 0
    duration_ms: int = 0
    junit_xml_path: Optional[str] = None             # path to the raw junit xml on disk


class TestCaseRef(BaseModel):
    """Lightweight reference to a test case in the generated plan."""

    test_case_id: str
    feature_or_function_tested: str
    scenario_type: str                               # 'Happy Path' | 'Error Case' | 'Edge Case'


class PipelineRun(BaseModel):
    """ADO pipeline-run record produced by Phase B.2 (trigger + poll)."""

    run_id: int
    pipeline_id: int
    project: str
    state: str                                       # "inProgress" | "completed" | "cancelling" | "timeout"
    result: Optional[str] = None                    # "succeeded" | "failed" | "canceled"
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    web_url: Optional[str] = None


class SecurityFinding(BaseModel):
    """One security finding from a static-analysis or supply-chain scanner.

    Phase B.3 originally introduced Trivy + Sonar sources from the ADO pipeline
    path. Phase 11.4 extended this to also accept findings from local
    static-analysis skills (bandit, eslint, dotnet analyzers, etc.) so the
    deployment agent's view of "security issues" covers BOTH pipeline-level
    container scans AND in-source code-quality findings.
    """

    source: Literal["trivy", "sonar", "bandit", "eslint", "dotnet-analyzer", "other"]
    severity: str                                    # "CRITICAL" | "HIGH" | "MEDIUM" | "LOW" | "INFO"
    rule_id: str
    file: str
    line: Optional[int] = None
    message: str
    cwe: Optional[str] = None


class FunctionalScenarioResult(BaseModel):
    """One HTTP scenario executed by the functional runner."""

    scenario_id: str                                  # e.g. "FS-001"
    method: str                                       # GET | POST | PUT | PATCH | DELETE
    path: str                                         # e.g. "/users/1"
    status_code_expected: int
    status_code_actual: Optional[int] = None
    passed: bool
    response_sample: Optional[str] = None             # truncated to ~500 chars
    error: Optional[str] = None


class DefectEntry(BaseModel):
    """One row in the QA report's defect log."""

    defect_id: str                                    # "DEF-001"
    test_case_ref: Optional[str] = None
    severity: Literal["critical", "high", "medium", "low"]
    summary: str
    stack_trace: Optional[str] = None
    reproducer: Optional[str] = None


class TestingArtifact(BaseModel):
    """Top-level artifact produced by the testing agent.

    Persisted to AgentSession.testing_artifacts (JSONField, see migration 0005).
    Consumed by the deployment agent and the UI.
    """

    # Phase 3 — base contract
    plan_test_case_count: int = 0
    test_cases: List[TestCaseRef] = Field(default_factory=list)
    generated_test_code: Optional[str] = None        # raw test-file source (per-language)
    test_execution: Optional[TestExecution] = None
    coverage: Optional[CoverageSummary] = None
    pylint_report: Optional[str] = None              # textual lint output (any language)
    summary_md: Optional[str] = None                 # human-readable Markdown summary
    artifact_files: List[str] = Field(default_factory=list)
    """Filenames (NOT paths) saved to the session output dir."""

    status: Literal[
        "not_started",
        "plan_only",
        "executed",
        "executed_with_failures",
        "failed",
        # Phase B.2 bug-fix — pipeline-only runs (no local pytest, no test cases)
        # land here when the ADO pipeline reaches a terminal state successfully.
        "pipeline_completed",
        "pipeline_failed",
    ] = "not_started"

    # Phase M.6 — multi-language metadata
    language: Literal["python", "dotnet", "react", "unknown"] = "unknown"
    runner_command: Optional[str] = None             # exact CLI used (so deployment can replay it)

    # Phase B.2 — pipeline trigger record (Optional so deployment formatter
    # tolerates absence on test-only runs)
    pipeline_run: Optional[PipelineRun] = None

    # Phase B.3 — Trivy/Sonar parsed findings
    security_findings: List[SecurityFinding] = Field(default_factory=list)

    # Phase 7 — PR-scoped coverage. Whole-repo % can be misleading (a 50-line
    # PR against a 10k-line app barely moves the needle). PR-scoped reports
    # what fraction of CHANGED lines are exercised by the tests — the metric
    # reviewers actually care about. Optional so deployment formatter tolerates
    # absence on uploads / non-PR runs.
    pr_coverage: Optional["PRCoverageSummary"] = None

    # Phase 10 (testing-agent enhancement) — additive fan-out fields.
    # All optional → deployment formatter tolerates absence on legacy runs.
    functional_results: List[FunctionalScenarioResult] = Field(default_factory=list)
    defect_log: List[DefectEntry] = Field(default_factory=list)
    qa_report_html_path: Optional[str] = None
    qa_report_pdf_path: Optional[str] = None

    # Phase 11.4 — shell-capable skill outputs. All optional + default-empty;
    # downstream formatters tolerate absence on legacy / no-shell-skill runs.
    mutation_results: Optional["MutationResult"] = None
    dependency_vulns: List["DependencyVulnerability"] = Field(default_factory=list)


class MutationResult(BaseModel):
    """Output of a mutation-testing skill (Stryker.NET / Stryker-JS / mutmut).

    Mutation testing introduces small bugs ("mutants") into the source and
    re-runs the test suite. A "killed" mutant is one detected by a failing
    test; a "survived" mutant slipped through (your test suite missed it).
    The kill-rate is the fundamental measure of test-suite quality — high
    coverage % with low kill-rate means you have lots of tests but they
    don't actually verify behaviour.
    """

    tool: str                                        # "stryker.net" | "stryker-js" | "mutmut"
    kill_rate_pct: float                             # 0..100
    mutants_total: int = 0
    mutants_killed: int = 0
    mutants_survived: int = 0
    mutants_timeout: int = 0
    mutants_no_coverage: int = 0
    top_survivors: List[Dict] = Field(default_factory=list)  # [{file, line, mutator, original, mutated}, …]
    duration_s: Optional[float] = None
    report_path: Optional[str] = None                # path to raw stryker.json / mutmut output


class DependencyVulnerability(BaseModel):
    """One package-vulnerability finding from pip-audit / dotnet list package
    --vulnerable / npm audit. Aggregated across language ecosystems."""

    source: Literal["pip-audit", "dotnet", "npm-audit"]
    package: str
    installed_version: Optional[str] = None
    severity: str                                    # "CRITICAL" | "HIGH" | "MEDIUM" | "LOW" | "UNKNOWN"
    cve: Optional[str] = None
    advisory_url: Optional[str] = None
    fix_versions: List[str] = Field(default_factory=list)
    summary: Optional[str] = None


class PRCoverageSummary(BaseModel):
    """Coverage restricted to lines the PR added/modified vs the base branch."""

    base_branch: str = "main"
    files_changed: int = 0                       # number of changed source files
    changed_lines_total: int = 0                 # total lines added/modified in the PR
    changed_lines_covered: int = 0               # subset of those exercised by tests
    coverage_pct: float = 0.0                    # 0..100
    untested_files: List[str] = Field(default_factory=list)  # files in the PR with 0% coverage


class GeneratedTestSet(BaseModel):
    """Output of one Skill's generation. Written to disk by _run_skill."""

    skill_name: str
    test_file_path: str                              # absolute path
    test_framework: str                              # pytest | xunit | jest
    scenario_count: int = 0


class SkillFailure(BaseModel):
    """One Skill's generation failure. Captured by asyncio.gather(return_exceptions=True)."""

    skill_name: str
    reason: str
    traceback: Optional[str] = None


class AggregatedResults(BaseModel):
    """Fan-in output: every test type's result merged into one structure."""

    generated_test_sets: List[GeneratedTestSet] = Field(default_factory=list)
    skill_failures: List[SkillFailure] = Field(default_factory=list)
    test_execution: Optional[TestExecution] = None      # combined unit + functional + smoke
    coverage: Optional[CoverageSummary] = None
    pr_coverage: Optional[PRCoverageSummary] = None
    functional_results: List[FunctionalScenarioResult] = Field(default_factory=list)
    ui_test_results: Optional[List[Dict]] = None        # raw from Selenium agent
    security_findings: List[SecurityFinding] = Field(default_factory=list)
    pipeline_run: Optional[PipelineRun] = None
    defect_log: List[DefectEntry] = Field(default_factory=list)
    # Phase 11.4 — shell-capable skill outputs
    mutation_results: Optional["MutationResult"] = None
    dependency_vulns: List["DependencyVulnerability"] = Field(default_factory=list)


# Forward-ref resolution for PRCoverageSummary on TestingArtifact
TestingArtifact.model_rebuild()
AggregatedResults.model_rebuild()
