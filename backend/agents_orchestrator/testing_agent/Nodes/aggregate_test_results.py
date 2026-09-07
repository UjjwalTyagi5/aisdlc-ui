"""Fan-in node: merges every source's results into one AggregatedResults
structure. Builds defect_log entries from skill_failures and from any failed
test_execution rows.

Phase 11.4 — also routes parsed artifacts from shell-runtime skills
(mutation_testing → mutation_results, dependency_scan / security_static →
dependency_vulns + security_findings) into the right AggregatedResults
fields.
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from shared.models import (
    AggregatedResults,
    DefectEntry,
    DependencyVulnerability,
    FunctionalScenarioResult,
    GeneratedTestSet,
    MutationResult,
    SecurityFinding,
    SkillFailure,
)

from agents_orchestrator.testing_agent.config.session_state import SuperAgentState
from agents_orchestrator.testing_agent.config.shared import blog
from agents_orchestrator.testing_agent.hooks import post_test_run

logger = logging.getLogger("testing_agent.aggregate")


_GENERATED_TEST_SET_FIELDS = {"skill_name", "test_file_path", "test_framework", "scenario_count"}


async def aggregate_test_results(state: SuperAgentState) -> Dict[str, Any]:
    blog("Aggregating test results...")

    raw_test_sets = state.get("generated_test_sets") or []

    # Phase 11.4 — shell skills include extra fields (runtime, parsed_artifact,
    # output_artifact_field, shell_exit_code) that aren't part of the
    # GeneratedTestSet schema. Filter to known fields when constructing the
    # pydantic model; route the parsed artifacts to dedicated fields below.
    # ONE UNUSABLE ENTRY MUST NOT DISCARD THE REST. This was a list comprehension, so a
    # single skill handing back `{}` — no skill_name, no test_file_path, no
    # test_framework — raised ValidationError out of the whole node and took every OTHER
    # skill's passing results, the defect log and the QA report down with it. Fanning out
    # to skills that fail independently is the entire point of dispatch; the fan-in has
    # to hold that property too. Built one at a time, and an entry that cannot be built
    # is recorded as a skill failure below rather than dropped silently — "the aggregator
    # skipped it" and "the skill produced nothing" must not look the same in the report.
    sets: list[GeneratedTestSet] = []
    malformed: list[str] = []
    for raw in raw_test_sets:
        if not isinstance(raw, dict):
            malformed.append(f"aggregate: discarded a non-dict test set ({type(raw).__name__})")
            continue
        try:
            sets.append(
                GeneratedTestSet(**{k: v for k, v in raw.items() if k in _GENERATED_TEST_SET_FIELDS})
            )
        except Exception as exc:  # noqa: BLE001 — the reason is carried into the report
            name = str(raw.get("skill_name") or "unknown")
            malformed.append(f"{name}: produced no usable test set ({exc.__class__.__name__})")
            logger.warning("aggregate: unusable test set from %s: %s", name, exc)

    failures = [
        SkillFailure(skill_name=f.split(":", 1)[0], reason=f)
        for f in list(state.get("skill_failures") or []) + malformed
    ]

    defects: list = []

    # Promote each skill_failure to a defect entry
    for i, sf in enumerate(failures, start=1):
        defects.append(DefectEntry(
            defect_id=f"DEF-SKILL-{i:03d}",
            severity="high",
            summary=f"{sf.skill_name} generation failed: {sf.reason}",
        ))

    # Functional scenario failures → defect entries
    func_results = [
        FunctionalScenarioResult(**r) if isinstance(r, dict) else r
        for r in (state.get("functional_results") or [])
    ]
    for i, fr in enumerate(func_results, start=1):
        if not fr.passed:
            defects.append(DefectEntry(
                defect_id=f"DEF-FUNC-{i:03d}",
                test_case_ref=fr.scenario_id,
                severity="high",
                summary=f"{fr.method} {fr.path}: expected {fr.status_code_expected}, got {fr.status_code_actual}",
                stack_trace=fr.error,
                reproducer=f"curl -X {fr.method} {fr.path}",
            ))

    # Phase 11.4 — route shell-skill parsed artifacts. Each shell skill
    # decorates its returned dict with `parsed_artifact` (dict or list of
    # dicts) + `output_artifact_field` (which AggregatedResults field to
    # populate). We map them into mutation_results / dependency_vulns /
    # security_findings (extending). Multiple shell skills can contribute
    # to the same field (e.g. bandit + dotnet analyzers both → security_findings).
    mutation_results = None
    dependency_vulns: list = []
    extra_security_findings: list = []

    for s in raw_test_sets:
        if s.get("runtime") != "shell":
            continue
        artifact = s.get("parsed_artifact")
        target_field = s.get("output_artifact_field")
        if artifact is None or not target_field:
            continue
        if target_field == "mutation_results" and isinstance(artifact, dict):
            try:
                mutation_results = MutationResult(**artifact)
            except Exception as exc:
                logger.warning(f"aggregate: invalid mutation_results from {s.get('skill_name')}: {exc}")
        elif target_field == "dependency_vulns" and isinstance(artifact, list):
            for v in artifact:
                if isinstance(v, dict):
                    try:
                        dependency_vulns.append(DependencyVulnerability(**v))
                    except Exception as exc:
                        logger.warning(f"aggregate: invalid DependencyVulnerability: {exc}")
        elif target_field == "security_findings" and isinstance(artifact, list):
            for f in artifact:
                if isinstance(f, dict):
                    try:
                        extra_security_findings.append(SecurityFinding(**f))
                    except Exception as exc:
                        logger.warning(f"aggregate: invalid SecurityFinding: {exc}")

    # Existing pipeline-path security findings (Trivy / Sonar) come from
    # state["security_findings"] — merge with shell-skill ones.
    pipeline_security = [
        SecurityFinding(**f) if isinstance(f, dict) else f
        for f in (state.get("security_findings") or [])
    ]
    all_security = pipeline_security + extra_security_findings

    aggregated = AggregatedResults(
        generated_test_sets=sets,
        skill_failures=failures,
        functional_results=func_results,
        ui_test_results=state.get("ui_test_results"),
        defect_log=defects,
        # Phase 11.4 additions
        mutation_results=mutation_results,
        dependency_vulns=dependency_vulns,
        security_findings=all_security,
    )

    summary_parts = [f"{len(sets)} test sets", f"{len(failures)} skill failures", f"{len(defects)} defects"]
    if mutation_results:
        summary_parts.append(f"{mutation_results.kill_rate_pct}% mutation kill-rate")
    if dependency_vulns:
        summary_parts.append(f"{len(dependency_vulns)} dependency vulns")
    if all_security:
        summary_parts.append(f"{len(all_security)} security findings")
    blog("Aggregated: " + ", ".join(summary_parts))

    # Hook may augment defect_log
    hook_delta = post_test_run(state, {"defect_log": [d.model_dump() for d in defects]}) or {}

    return {
        "aggregated_results": aggregated.model_dump(),
        "defect_log": hook_delta.get("defect_log") or [d.model_dump() for d in defects],
    }
