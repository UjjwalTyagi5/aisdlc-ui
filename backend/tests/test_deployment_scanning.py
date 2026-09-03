"""Scanning the code the pipeline ships — PHASE 2b.

TWO FAILURE MODES, and this file is mostly about them.

The first is a scan stage that cannot run. Dropping it silently means nobody knows the
code is unscanned; writing it anyway means the pipeline fails on its first run and
somebody disables the stage. Both end in unscanned code with a green tick, so every scan
lands in `stages` or in `not_configured` — never in neither.

The second is treating UNMEASURED as PASSED. `None` means nothing looked, and a release
decision that reads it as a pass is the most expensive bug in the file.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents_orchestrator.deployment_agent.scanning import (  # noqa: E402
    gate_verdict, plan_scans,
)


def _ids(plan, key="stages"):
    return {s["id"] for s in plan[key]}


def _stage(plan, sid):
    return next(s for s in plan["stages"] if s["id"] == sid)


def _reason(plan, sid):
    return next(n["reason"] for n in plan["not_configured"] if n["id"] == sid)


# -- nothing goes missing ------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("via", ["azure_pipelines", "github_actions", "jenkins", ""])
def test_every_scan_is_either_planned_or_explained(via):
    """THE INVARIANT. A scan that appears in neither list is one nobody knows is
    missing."""
    plan = plan_scans("node", via, has_dockerfile=True, sonar_configured=True)
    accounted = _ids(plan) | _ids(plan, "not_configured")
    assert {"sonarqube", "dependency_scan", "container_scan", "security_devops",
            "codeql"} <= accounted


@pytest.mark.unit
def test_everything_that_cannot_run_says_why():
    plan = plan_scans(None, "", has_dockerfile=False, sonar_configured=False)
    assert plan["not_configured"]
    assert all(n.get("reason") for n in plan["not_configured"])


# -- SonarQube -----------------------------------------------------------------


@pytest.mark.unit
def test_no_sonar_connector_means_no_sonar_stage_and_a_reason():
    """A Sonar stage written for a tenant with no Sonar fails on first run, and the
    fix somebody reaches for is deleting the stage."""
    plan = plan_scans("node", "azure_pipelines", sonar_configured=False)
    assert "sonarqube" not in _ids(plan)
    assert "Integrations page" in _reason(plan, "sonarqube")


@pytest.mark.unit
@pytest.mark.parametrize("via", ["azure_pipelines", "github_actions", "jenkins"])
def test_sonar_is_planned_for_every_ci_system_it_supports(via):
    plan = plan_scans("node", via, sonar_configured=True)
    assert "sonarqube" in _ids(plan)


@pytest.mark.unit
def test_the_sonar_stage_actually_breaks_the_build():
    """Without a build breaker the quality gate is advisory, and a failing gate
    ships."""
    plan = plan_scans("node", "azure_pipelines", sonar_configured=True)
    stage = _stage(plan, "sonarqube")
    assert stage["blocking"] is True
    assert "buildbreaker" in stage["tasks"].lower()


@pytest.mark.unit
def test_the_github_sonar_stage_waits_for_the_gate():
    plan = plan_scans("node", "github_actions", sonar_configured=True)
    assert "quality-gate-action" in _stage(plan, "sonarqube")["tasks"]


@pytest.mark.unit
def test_the_real_project_key_is_used_when_known():
    plan = plan_scans("node", "azure_pipelines", sonar_configured=True,
                      sonar_project_key="acme_web")
    assert "acme_web" in _stage(plan, "sonarqube")["tasks"]


# -- dependencies are per-ecosystem -------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("stack,expected", [
    ("dotnet", "dotnet list package"),
    ("node", "npm audit"),
    ("python", "pip-audit"),
    ("java_maven", "dependency-check"),
    ("java_gradle", "dependencyCheck"),
    ("go", "govulncheck"),
])
def test_each_ecosystem_gets_its_own_scanner(stack, expected):
    """Running npm audit on a Maven repo is a passing stage that checked nothing."""
    plan = plan_scans(stack, "azure_pipelines", sonar_configured=True)
    assert expected in _stage(plan, "dependency_scan")["tasks"]


@pytest.mark.unit
def test_an_unknown_stack_gets_no_dependency_scan_and_says_so():
    plan = plan_scans(None, "azure_pipelines", sonar_configured=True)
    assert "dependency_scan" not in _ids(plan)
    assert "per-ecosystem" in _reason(plan, "dependency_scan")


# -- container -----------------------------------------------------------------


@pytest.mark.unit
def test_an_image_is_scanned_when_there_is_one():
    plan = plan_scans("go", "azure_pipelines", has_dockerfile=True,
                      sonar_configured=True)
    assert "trivy" in _stage(plan, "container_scan")["tasks"].lower()


@pytest.mark.unit
def test_no_image_means_no_image_scan_rather_than_a_stage_with_nothing_to_do():
    plan = plan_scans("go", "azure_pipelines", has_dockerfile=False,
                      sonar_configured=True)
    assert "container_scan" not in _ids(plan)
    assert "no image to scan" in _reason(plan, "container_scan")


@pytest.mark.unit
def test_the_image_scan_fails_the_build_on_critical_findings():
    plan = plan_scans("go", "azure_pipelines", has_dockerfile=True,
                      sonar_configured=True)
    tasks = _stage(plan, "container_scan")["tasks"]
    assert "--exit-code 1" in tasks and "CRITICAL" in tasks


# -- secrets / IaC and CodeQL --------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("via", ["azure_pipelines", "github_actions"])
def test_secrets_and_iac_scanning_is_added_where_it_exists(via):
    plan = plan_scans("node", via, sonar_configured=True)
    assert "security_devops" in _ids(plan)


@pytest.mark.unit
def test_msdo_is_not_pretended_to_exist_on_jenkins():
    plan = plan_scans("node", "jenkins", sonar_configured=True)
    assert "security_devops" not in _ids(plan)
    assert "jenkins" in _reason(plan, "security_devops").lower()


@pytest.mark.unit
def test_the_first_msdo_run_does_not_block_the_build():
    """A first run against an existing repo finds pre-existing issues, and a blocking
    stage there gets disabled rather than fixed."""
    plan = plan_scans("node", "azure_pipelines", sonar_configured=True)
    stage = _stage(plan, "security_devops")
    assert stage["blocking"] is False
    assert "blocking once the backlog is clear" in stage["note"]


@pytest.mark.unit
def test_codeql_is_not_assumed_to_be_licensed():
    """GHAS is billed separately. A CodeQL stage on a repo without it fails on first
    run."""
    plan = plan_scans("node", "github_actions", sonar_configured=True)
    assert "codeql" not in _ids(plan)
    assert "Advanced Security" in _reason(plan, "codeql")


@pytest.mark.unit
def test_codeql_is_added_when_it_is_available():
    plan = plan_scans("node", "github_actions", sonar_configured=True,
                      ghas_available=True)
    assert "codeql" in _ids(plan)


# -- unmeasured is not passed --------------------------------------------------


@pytest.mark.unit
def test_nothing_measured_is_never_a_go():
    """THE MOST EXPENSIVE BUG THIS FILE PREVENTS."""
    v = gate_verdict(None)
    assert v["release_decision"] == "conditional"
    assert v["blocking"] == []
    assert v["unmeasured"]


@pytest.mark.unit
def test_a_failing_quality_gate_blocks_the_release():
    v = gate_verdict({"status": "ERROR", "conditions": [
        {"metric": "new_coverage", "status": "ERROR"}]})
    assert v["release_decision"] == "no_go"


@pytest.mark.unit
def test_it_names_which_condition_failed():
    """"The quality gate failed" leaves somebody clicking through SonarQube."""
    v = gate_verdict({"status": "ERROR", "conditions": [
        {"metric": "new_coverage", "status": "ERROR"},
        {"metric": "new_bugs", "status": "OK"}]})
    assert "new_coverage" in v["justification"]
    assert "new_bugs" not in v["justification"]


@pytest.mark.unit
def test_a_passing_gate_with_everything_else_known_is_a_go():
    v = gate_verdict({"status": "OK"}, critical_vulnerabilities=0, tests_passing=True)
    assert v["release_decision"] == "go"
    assert v["unmeasured"] == []


@pytest.mark.unit
def test_a_passing_gate_alone_is_only_conditional():
    """The gate passing says nothing about the tests nobody ran."""
    v = gate_verdict({"status": "OK"})
    assert v["release_decision"] == "conditional"


@pytest.mark.unit
def test_an_unrecognised_gate_status_is_an_unknown_not_a_pass():
    v = gate_verdict({"status": "SOMETHING_NEW"})
    assert v["release_decision"] == "conditional"
    assert any("neither a pass nor a failure" in u for u in v["unmeasured"])


@pytest.mark.unit
def test_a_critical_vulnerability_blocks():
    v = gate_verdict({"status": "OK"}, critical_vulnerabilities=2, tests_passing=True)
    assert v["release_decision"] == "no_go"
    assert "2 critical vulnerabilities" in v["justification"]


@pytest.mark.unit
def test_one_vulnerability_is_not_described_as_vulnerabilities():
    v = gate_verdict({"status": "OK"}, critical_vulnerabilities=1, tests_passing=True)
    assert "1 critical vulnerability" in v["justification"]


@pytest.mark.unit
def test_zero_vulnerabilities_is_a_measurement_not_a_gap():
    """0 and None are different answers, and only one of them means somebody looked."""
    assert gate_verdict({"status": "OK"}, critical_vulnerabilities=0,
                        tests_passing=True)["release_decision"] == "go"
    assert gate_verdict({"status": "OK"}, critical_vulnerabilities=None,
                        tests_passing=True)["release_decision"] == "conditional"


@pytest.mark.unit
def test_failing_tests_block():
    v = gate_verdict({"status": "OK"}, critical_vulnerabilities=0, tests_passing=False)
    assert v["release_decision"] == "no_go"
    assert "Tests are failing" in v["justification"]


@pytest.mark.unit
def test_a_blocker_outranks_an_unknown():
    """A failure and a gap together is still a failure — not a conditional."""
    v = gate_verdict({"status": "ERROR"}, critical_vulnerabilities=None)
    assert v["release_decision"] == "no_go"


@pytest.mark.unit
def test_a_scan_that_did_not_run_is_carried_into_the_verdict():
    v = gate_verdict({"status": "OK"}, critical_vulnerabilities=0, tests_passing=True,
                     unscanned=["Container scanning"])
    assert v["release_decision"] == "conditional"
    assert any("Container scanning" in u for u in v["unmeasured"])


# -- the snippets survive being generated -------------------------------------


@pytest.mark.unit
def test_github_actions_expression_syntax_is_not_mangled():
    """`${{ secrets.SONAR_TOKEN }}` is GitHub syntax. str.format rewrites it to
    `${ secrets.SONAR_TOKEN }`, which is a workflow that cannot read its own token —
    and it does it silently."""
    tasks = _stage(plan_scans("node", "github_actions", sonar_configured=True),
                   "sonarqube")["tasks"]
    assert "${{ secrets.SONAR_TOKEN }}" in tasks


@pytest.mark.unit
def test_the_trivy_github_step_keeps_its_expressions():
    tasks = _stage(plan_scans("go", "github_actions", has_dockerfile=True,
                              sonar_configured=True), "container_scan")["tasks"]
    assert "${{ github.sha }}" in tasks


@pytest.mark.unit
def test_a_jenkins_stage_block_is_generated_at_all():
    """Groovy braces made str.format raise, which lost the whole Sonar stage."""
    tasks = _stage(plan_scans("node", "jenkins", sonar_configured=True),
                   "sonarqube")["tasks"]
    assert "withSonarQubeEnv" in tasks
    assert "waitForQualityGate abortPipeline: true" in tasks


@pytest.mark.unit
def test_the_project_key_is_substituted_in_every_ci_system():
    for via, needle in (("azure_pipelines", "cliProjectKey: 'acme_web'"),
                        ("jenkins", "-Dsonar.projectKey=acme_web")):
        tasks = _stage(plan_scans("node", via, sonar_configured=True,
                                  sonar_project_key="acme_web"), "sonarqube")["tasks"]
        assert needle in tasks, via


@pytest.mark.unit
def test_no_placeholder_token_survives_into_the_output():
    """A literal __KEY__ in a generated pipeline is a broken pipeline."""
    for via in ("azure_pipelines", "github_actions", "jenkins"):
        plan = plan_scans("node", via, has_dockerfile=True, sonar_configured=True,
                          sonar_project_key="acme_web")
        for stage in plan["stages"]:
            assert "__" not in stage["tasks"], (via, stage["id"])


@pytest.mark.unit
def test_a_specific_sonar_problem_is_reported_instead_of_the_generic_one():
    """Three different problems have three different fixes: no connector, no grant for
    this agent, and a connector that will not answer. "Connect SonarQube" sends someone
    to configure a thing that is already configured."""
    plan = plan_scans("node", "azure_pipelines", sonar_configured=False,
                      sonar_unavailable_reason="not granted to the Deployment agent")
    assert _reason(plan, "sonarqube") == "not granted to the Deployment agent"


@pytest.mark.unit
def test_the_generic_reason_is_still_used_when_there_is_nothing_more_specific():
    plan = plan_scans("node", "azure_pipelines", sonar_configured=False)
    assert "Integrations page" in _reason(plan, "sonarqube")
