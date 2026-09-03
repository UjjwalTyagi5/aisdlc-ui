"""Quality and vulnerability scanning in the generated pipeline — phase 2b.

A pipeline that ships code without looking at it is half a pipeline. This module
decides WHICH scan stages belong in the generated file, emits the concrete tasks for
the bound CI system, and turns the evidence that comes back into a release verdict.

THE RULE THAT SHAPES ALL OF IT. Not every project has SonarQube, GitHub Advanced
Security, or a licence for the Microsoft Security DevOps task. A scan stage that cannot
run has exactly two wrong answers — drop it silently, so nobody knows the code is
unscanned; or emit it anyway, so the pipeline fails on its first run and somebody
disables the whole stage. Both end with unscanned code and a green tick. So a scan that
cannot run is reported `not_configured`, with what to connect to make it work.

A FAILING GATE IS A NO. `gate_verdict` is deterministic on purpose: whether a failing
quality gate blocks a release is not a judgement call to be re-made, in prose, per turn.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

#: Per-stack dependency vulnerability scanning. The command differs by ecosystem, and
#: running the wrong one produces a passing stage that scanned nothing.
_DEP_SCAN: Dict[str, Dict[str, str]] = {
    "dotnet": {
        "tool": "dotnet list package --vulnerable",
        "script": "dotnet list package --vulnerable --include-transitive",
    },
    "node": {"tool": "npm audit", "script": "npm audit --audit-level=high"},
    "python": {"tool": "pip-audit", "script": "pip install pip-audit && pip-audit"},
    "java_maven": {
        "tool": "OWASP dependency-check",
        "script": "mvn -B org.owasp:dependency-check-maven:check",
    },
    "java_gradle": {
        "tool": "OWASP dependency-check",
        "script": "./gradlew dependencyCheckAnalyze",
    },
    "go": {"tool": "govulncheck",
           "script": "go install golang.org/x/vuln/cmd/govulncheck@latest && govulncheck ./..."},
}

_SONAR_ADO = """- task: SonarQubePrepare@6
  inputs:
    SonarQube: '$(sonarServiceConnection)'
    scannerMode: 'CLI'
    configMode: 'manual'
    cliProjectKey: '__KEY__'
- task: SonarQubeAnalyze@6
- task: SonarQubePublish@6
  inputs:
    pollingTimeoutSec: '300'
# Fails the build when the quality gate fails. Without it the gate is advisory
# and a failing gate ships.
- task: sonar-buildbreaker@8
  inputs:
    SonarQube: '$(sonarServiceConnection)'"""

_SONAR_GHA = """- name: SonarQube scan
  uses: sonarsource/sonarqube-scan-action@v3
  env:
    SONAR_TOKEN: ${{ secrets.SONAR_TOKEN }}
    SONAR_HOST_URL: ${{ secrets.SONAR_HOST_URL }}
- name: SonarQube quality gate
  uses: sonarsource/sonarqube-quality-gate-action@v1
  timeout-minutes: 5
  env:
    SONAR_TOKEN: ${{ secrets.SONAR_TOKEN }}"""

_SONAR_JENKINS = """stage('SonarQube') {
  steps {
    withSonarQubeEnv('sonarqube') {
      sh 'sonar-scanner -Dsonar.projectKey=__KEY__'
    }
  }
}
stage('Quality gate') {
  steps { timeout(time: 5, unit: 'MINUTES') { waitForQualityGate abortPipeline: true } }
}"""

_MSDO_ADO = """- task: MicrosoftSecurityDevOps@1
  displayName: 'Security scan (credentials, IaC, containers)'
  inputs:
    categories: 'secrets,IaC,containers'"""

_MSDO_GHA = """- name: Microsoft Security DevOps
  uses: microsoft/security-devops-action@v1
  with:
    categories: 'secrets,IaC,containers'"""

_TRIVY_ADO = """- script: |
    docker run --rm -v /var/run/docker.sock:/var/run/docker.sock \\
      aquasec/trivy:latest image --exit-code 1 \\
      --severity CRITICAL,HIGH $(imageName):$(Build.BuildId)
  displayName: 'Scan image for vulnerabilities (Trivy)'"""

_TRIVY_GHA = """- name: Scan image for vulnerabilities
  uses: aquasecurity/trivy-action@master
  with:
    image-ref: '${{ env.IMAGE_NAME }}:${{ github.sha }}'
    exit-code: '1'
    severity: 'CRITICAL,HIGH'"""


def _snippet(options: Dict[str, str], via: str, **fmt: Any) -> Optional[str]:
    """Pick the snippet for this CI system and substitute placeholders.

    NOT str.format. These snippets are YAML and Groovy, both full of braces:
    `${{ secrets.SONAR_TOKEN }}` is GitHub Actions syntax that format() silently
    rewrites to `${ secrets.SONAR_TOKEN }`, and a Jenkins stage block raises outright.
    A plain replace of an unmistakable token cannot collide with either.
    """
    body = options.get(via)
    if not body:
        return None
    for name, value in fmt.items():
        body = body.replace(f"__{name.upper()}__", str(value))
    return body


def plan_scans(
    stack_kind: Optional[str],
    deploy_via: str = "",
    *,
    has_dockerfile: bool = False,
    sonar_configured: bool = False,
    sonar_project_key: str = "",
    sonar_unavailable_reason: str = "",
    ghas_available: bool = False,
) -> Dict[str, Any]:
    """Decide the scan stages for this repo's generated pipeline.

    Every scan lands in exactly one of two lists. `stages` is what goes into the file.
    `not_configured` is what does not, and what to connect so that it can — never
    silence, because unscanned code that nobody flagged is the outcome this whole
    module exists to avoid.
    """
    via = (deploy_via or "").strip().lower()
    stages: List[Dict[str, Any]] = []
    not_configured: List[Dict[str, str]] = []

    # ── code quality: SonarQube ───────────────────────────────────────────
    if not sonar_configured:
        # THREE DIFFERENT PROBLEMS WITH THREE DIFFERENT FIXES: no connector, a
        # connector this agent has no grant for, and a connector that will not answer.
        # Collapsing them into "connect SonarQube" sends someone to configure a thing
        # that is already configured.
        not_configured.append({
            "id": "sonarqube",
            "reason": sonar_unavailable_reason or (
                "No SonarQube connector is configured for this tenant. Connect one on "
                "the Integrations page; a Sonar stage written now would fail on the "
                "pipeline's first run."
            ),
        })
    else:
        body = _snippet(
            {"azure_pipelines": _SONAR_ADO, "github_actions": _SONAR_GHA,
             "jenkins": _SONAR_JENKINS},
            via, key=sonar_project_key or "$(sonarProjectKey)",
        )
        if body:
            stages.append({
                "id": "sonarqube", "name": "Code quality (SonarQube)",
                "blocking": True, "tasks": body,
                "note": "The build breaker is what makes the gate real. Without it a "
                        "failing quality gate is advisory and ships anyway.",
            })
        else:
            not_configured.append({
                "id": "sonarqube",
                "reason": f"No SonarQube step is defined for deploy_via={via or 'unknown'}.",
            })

    # ── dependencies ──────────────────────────────────────────────────────
    dep = _DEP_SCAN.get(stack_kind or "")
    if dep is None:
        not_configured.append({
            "id": "dependency_scan",
            "reason": "The stack is unknown, and dependency scanning is per-ecosystem. "
                      "Running the wrong scanner produces a passing stage that "
                      "checked nothing.",
        })
    elif via in ("azure_pipelines", "github_actions", "jenkins"):
        stages.append({
            "id": "dependency_scan",
            "name": f"Dependency vulnerabilities ({dep['tool']})",
            "blocking": True,
            "tasks": (f"- script: |\n    {dep['script']}\n"
                      f"  displayName: 'Dependency vulnerabilities ({dep['tool']})'"
                      if via == "azure_pipelines" else
                      f"- name: Dependency vulnerabilities ({dep['tool']})\n"
                      f"  run: {dep['script']}"
                      if via == "github_actions" else
                      f"stage('Dependencies') {{ steps {{ sh '{dep['script']}' }} }}"),
        })
    else:
        # A known stack with no CI system to run it in. This branch existed as a silent
        # fall-through, which is precisely the invariant this module is built on: a scan
        # in neither list is one nobody knows is missing.
        not_configured.append({
            "id": "dependency_scan",
            "reason": f"{dep['tool']} is the right scanner for this stack, but no CI "
                      f"system is bound to run it in. Bind a deploy connector first.",
        })

    # ── container image ───────────────────────────────────────────────────
    if not has_dockerfile:
        not_configured.append({
            "id": "container_scan",
            "reason": "No container image is built by this package, so there is no "
                      "image to scan.",
        })
    else:
        body = _snippet({"azure_pipelines": _TRIVY_ADO, "github_actions": _TRIVY_GHA},
                        via)
        if body:
            stages.append({
                "id": "container_scan", "name": "Container image (Trivy)",
                "blocking": True, "tasks": body,
            })
        else:
            not_configured.append({
                "id": "container_scan",
                "reason": f"No image scan step is defined for deploy_via="
                          f"{via or 'unknown'}.",
            })

    # ── secrets / IaC, via the platform's own umbrella task ───────────────
    body = _snippet({"azure_pipelines": _MSDO_ADO, "github_actions": _MSDO_GHA}, via)
    if body:
        stages.append({
            "id": "security_devops", "name": "Secrets and IaC (Microsoft Security DevOps)",
            "blocking": False, "tasks": body,
            "note": "Non-blocking by default: MSDO reports broadly and a first run "
                    "against an existing repo usually finds pre-existing issues. "
                    "Make it blocking once the backlog is clear.",
        })
    else:
        not_configured.append({
            "id": "security_devops",
            "reason": "Microsoft Security DevOps runs on Azure Pipelines and GitHub "
                      "Actions. There is no equivalent step for "
                      f"{via or 'an unbound connector'}.",
        })

    # ── CodeQL, licence-gated ─────────────────────────────────────────────
    if ghas_available and via == "github_actions":
        stages.append({
            "id": "codeql", "name": "CodeQL", "blocking": True,
            "tasks": "- name: Initialize CodeQL\n  uses: github/codeql-action/init@v3\n"
                     "- name: Analyze\n  uses: github/codeql-action/analyze@v3",
        })
    else:
        not_configured.append({
            "id": "codeql",
            "reason": "CodeQL needs GitHub Advanced Security — on GitHub natively, or "
                      "GHAS for Azure DevOps. It is licensed separately and is not "
                      "assumed to be present.",
        })

    return {
        "deploy_via": via or None,
        "stack": stack_kind,
        "stages": stages,
        "not_configured": not_configured,
        "summary": (
            f"{len(stages)} scan stage(s) to add, {len(not_configured)} that cannot "
            f"run here."
        ),
    }


def gate_verdict(
    quality_gate: Optional[Dict[str, Any]] = None,
    *,
    critical_vulnerabilities: Optional[int] = None,
    tests_passing: Optional[bool] = None,
    unscanned: Sequence[str] = (),
) -> Dict[str, Any]:
    """Turn gate evidence into a release verdict.

    DETERMINISTIC ON PURPOSE. Whether a failing quality gate blocks a release is not a
    judgement to be re-made in prose each turn, and a model that has just written a
    thousand lines of pipeline is not the thing you want deciding it.

    `None` means NOT MEASURED, and is never read as a pass. That distinction is the
    whole point: "no critical vulnerabilities were found" and "nothing looked" are
    different sentences, and only one of them is a reason to ship.
    """
    blocking: List[str] = []
    unknowns: List[str] = []

    status = str((quality_gate or {}).get("status") or "").upper()
    if not quality_gate or not status:
        unknowns.append("No SonarQube quality gate was read.")
    elif status in ("ERROR", "FAILED"):
        failed = [
            c.get("metric", "?")
            for c in (quality_gate.get("conditions") or [])
            if str(c.get("status", "")).upper() in ("ERROR", "FAILED")
        ]
        blocking.append(
            "The SonarQube quality gate failed"
            + (f" on {', '.join(failed)}." if failed else ".")
        )
    elif status not in ("OK", "PASSED"):
        unknowns.append(f"The quality gate reported {status!r}, which is neither a "
                        "pass nor a failure.")

    if critical_vulnerabilities is None:
        unknowns.append("Vulnerability count was not measured.")
    elif critical_vulnerabilities > 0:
        blocking.append(
            f"{critical_vulnerabilities} critical vulnerabilit"
            f"{'y' if critical_vulnerabilities == 1 else 'ies'} unresolved."
        )

    if tests_passing is None:
        unknowns.append("No test result was available.")
    elif tests_passing is False:
        blocking.append("Tests are failing.")

    for scan in unscanned:
        unknowns.append(f"{scan} did not run, so that class of problem is unmeasured.")

    if blocking:
        decision, why = "no_go", blocking
    elif unknowns:
        decision, why = "conditional", unknowns
    else:
        decision, why = "go", ["Every gate that was read passed."]

    return {
        "release_decision": decision,
        "blocking": blocking,
        "unmeasured": unknowns,
        "justification": " ".join(why),
    }
