"""Deciding the deployment package — PHASE 2.

THE RULES HERE HAVE RIGHT ANSWERS, which is exactly why they are code. A model asked to
hold them all at once eventually emits Kubernetes manifests beside a Helm chart that
already renders them, or a Dockerfile whose base image it guessed from nothing.

The tests that matter most are the ones about REFUSING: an unrecognised stack, an
unbound CI connector, and a chart that already owns the manifests. Each returns a
question rather than a plausible file.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents_orchestrator.deployment_agent.packaging import (  # noqa: E402
    detect_stack, plan_package,
)


def _m(**kw):
    """Markers as inspect_repo reports them — everything empty unless named."""
    base = {k: [] for k in (
        "dockerfile", "k8s_manifests", "helm", "argocd", "azure_pipelines",
        "github_actions", "csproj", "package_json", "requirements_txt", "go_mod",
        "migrations", "pom_xml", "build_gradle", "jenkinsfile", "docker_compose",
    )}
    base.update({k: (v if isinstance(v, list) else [v]) for k, v in kw.items()})
    return base


def _paths(plan, kind=None):
    return [f["path"] for f in plan["files"] if kind is None or f["kind"] == kind]


def _action(plan, path):
    return next(f["action"] for f in plan["files"] if f["path"] == path)


# -- stack detection -----------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("marker,expected", [
    ("csproj", "dotnet"),
    ("pom_xml", "java_maven"),
    ("build_gradle", "java_gradle"),
    ("go_mod", "go"),
    ("requirements_txt", "python"),
    ("package_json", "node"),
])
def test_each_stack_is_recognised(marker, expected):
    assert detect_stack(_m(**{marker: "f"}))["kind"] == expected


@pytest.mark.unit
def test_java_is_recognised_at_all():
    """It was not. pom.xml and build.gradle had no marker bucket, so every Maven and
    Gradle repo reported an unknown stack and got no Dockerfile."""
    assert detect_stack(_m(pom_xml="pom.xml"))["kind"] == "java_maven"


@pytest.mark.unit
def test_a_frontend_package_json_does_not_turn_a_dotnet_service_into_node():
    """Service repos routinely carry a package.json for asset building. First match
    wins, in dependency order."""
    assert detect_stack(_m(csproj="Api/Api.csproj", package_json="web/package.json")
                        )["kind"] == "dotnet"


@pytest.mark.unit
def test_an_unrecognised_repo_says_so_rather_than_picking_one():
    stack = detect_stack(_m())
    assert stack["kind"] is None
    assert "unknown" in stack["reason"].lower()


# -- refusing to guess ---------------------------------------------------------


@pytest.mark.unit
def test_no_dockerfile_is_written_for_a_stack_it_cannot_identify():
    """A base image guessed from nothing is a build failure at best, and the wrong
    runtime in production at worst."""
    plan = plan_package(_m(), "azure_pipelines")
    assert "Dockerfile" not in _paths(plan)
    assert any(u["path"] == "Dockerfile" for u in plan["undecided"])


@pytest.mark.unit
def test_the_undecided_dockerfile_says_what_to_ask():
    plan = plan_package(_m(), "azure_pipelines")
    reason = next(u["reason"] for u in plan["undecided"] if u["path"] == "Dockerfile")
    assert "ask which runtime" in reason.lower()


@pytest.mark.unit
def test_with_no_connector_bound_it_refuses_to_pick_a_ci_file():
    """Emitting azure-pipelines.yml, a workflow and a Jenkinsfile together is not a
    package, it is a mess somebody has to clean up."""
    plan = plan_package(_m(package_json="package.json"), "")
    assert _paths(plan, "ci") == []
    assert any("CI pipeline" in u["path"] for u in plan["undecided"])


@pytest.mark.unit
def test_it_does_not_emit_every_ci_file_just_in_case():
    plan = plan_package(_m(package_json="package.json"), "")
    everything = " ".join(_paths(plan))
    assert "azure-pipelines" not in everything
    assert "Jenkinsfile" not in everything
    assert "workflows" not in everything


# -- one source of truth -------------------------------------------------------


@pytest.mark.unit
def test_a_helm_chart_stops_raw_manifests_being_generated_beside_it():
    """Two sources of truth for the same Deployment drift, and the drift is discovered
    in production."""
    plan = plan_package(_m(package_json="package.json", helm="chart/Chart.yaml"),
                        "azure_pipelines")
    assert "deploy/deployment.yaml" not in _paths(plan)
    assert any("Helm chart already renders" in n["reason"]
               for n in plan["not_applicable"])


@pytest.mark.unit
def test_an_existing_manifest_is_refreshed_not_duplicated():
    plan = plan_package(_m(package_json="p", k8s_manifests="k8s/deploy.yaml"),
                        "azure_pipelines")
    assert _action(plan, "k8s/deploy.yaml") == "refresh"
    assert "deploy/deployment.yaml" not in _paths(plan)


@pytest.mark.unit
def test_an_existing_dockerfile_is_left_alone():
    """It is a file the team maintains. Replacing it loses whatever it encodes."""
    plan = plan_package(_m(package_json="p", dockerfile="Dockerfile"), "github_actions")
    assert "Dockerfile" not in _paths(plan)
    assert any(n["path"] == "Dockerfile" for n in plan["not_applicable"])


@pytest.mark.unit
def test_an_existing_pipeline_is_refreshed_at_its_real_path():
    """Writing azure-pipelines.yml when the repo keeps it at ci/azure-pipelines.yml
    creates a second pipeline definition nobody asked for."""
    plan = plan_package(_m(csproj="a.csproj", azure_pipelines="ci/azure-pipelines.yml"),
                        "azure_pipelines")
    assert "ci/azure-pipelines.yml" in _paths(plan, "ci")
    assert _action(plan, "ci/azure-pipelines.yml") == "refresh"


# -- the CI file follows the connector ----------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("via,expected", [
    ("azure_pipelines", "azure-pipelines.yml"),
    ("github_actions", ".github/workflows/deploy.yml"),
    ("jenkins", "Jenkinsfile"),
])
def test_the_bound_connector_decides_the_ci_file(via, expected):
    plan = plan_package(_m(go_mod="go.mod"), via)
    assert _paths(plan, "ci") == [expected]


@pytest.mark.unit
def test_argocd_writes_an_application_manifest():
    plan = plan_package(_m(go_mod="go.mod"), "argocd")
    assert _paths(plan, "ci") == ["deploy/argocd/application.yaml"]


@pytest.mark.unit
def test_argocd_uses_the_path_the_repo_already_has():
    plan = plan_package(_m(go_mod="go.mod", argocd="ops/argocd"), "argocd")
    assert _paths(plan, "ci") == ["ops/argocd/application.yaml"]
    assert _action(plan, "ops/argocd/application.yaml") == "refresh"


@pytest.mark.unit
def test_a_jenkinsfile_can_be_asked_for_alongside_another_connector():
    """A team can run ADO for builds and Jenkins for something else. Asking for one
    should not silently replace the bound connector's file."""
    plan = plan_package(_m(go_mod="go.mod"), "azure_pipelines", also=["jenkins"])
    ci = _paths(plan, "ci")
    assert "Jenkinsfile" in ci and "azure-pipelines.yml" in ci


# -- nothing optional appears uninvited ---------------------------------------


@pytest.mark.unit
def test_helm_is_not_imposed_on_a_repo_that_does_not_use_it():
    """Introducing Helm is a migration, not a deployment artifact."""
    plan = plan_package(_m(package_json="p"), "github_actions")
    assert not any("chart" in p.lower() for p in _paths(plan))


@pytest.mark.unit
def test_helm_is_generated_when_asked_for():
    plan = plan_package(_m(package_json="p"), "github_actions", also=["helm"])
    assert "deploy/chart/Chart.yaml" in _paths(plan)
    assert "deploy/chart/templates/deployment.yaml" in _paths(plan)


@pytest.mark.unit
def test_compose_is_not_generated_uninvited():
    plan = plan_package(_m(package_json="p"), "github_actions")
    assert not any("compose" in p for p in _paths(plan))


@pytest.mark.unit
def test_an_existing_compose_file_is_named_as_out_of_scope_not_ignored():
    """Silence would read as an oversight to anyone reviewing the PR."""
    plan = plan_package(_m(package_json="p", docker_compose="docker-compose.yml"),
                        "github_actions")
    assert any("compose" in n["path"] for n in plan["not_applicable"])


# -- what is always there ------------------------------------------------------


@pytest.mark.unit
def test_every_package_carries_a_way_forward_and_a_way_back():
    plan = plan_package(_m(go_mod="go.mod"), "azure_pipelines")
    assert "deploy/deploy-runbook.md" in _paths(plan)
    assert "deploy/rollback-runbook.md" in _paths(plan)


@pytest.mark.unit
def test_runbooks_are_produced_even_when_everything_else_is_undecided():
    """A repo it cannot package still deserves the runbooks it can write."""
    plan = plan_package(_m(), "")
    assert "deploy/rollback-runbook.md" in _paths(plan)


# -- risks that change the runbook --------------------------------------------


@pytest.mark.unit
def test_database_migrations_are_flagged_against_the_rollback():
    """Redeploying the old image against a migrated schema is not a rollback."""
    plan = plan_package(_m(csproj="a.csproj", migrations="src/Migrations"),
                        "azure_pipelines")
    assert any("backward-compatible" in w for w in plan["warnings"])


@pytest.mark.unit
def test_production_is_called_out():
    plan = plan_package(_m(go_mod="go.mod"), "azure_pipelines", environment="prod")
    assert any("production" in w.lower() for w in plan["warnings"])


@pytest.mark.unit
def test_a_quiet_repo_produces_no_invented_warnings():
    plan = plan_package(_m(go_mod="go.mod"), "azure_pipelines", environment="dev")
    assert plan["warnings"] == []


# -- shape ---------------------------------------------------------------------


@pytest.mark.unit
def test_every_file_says_why_it_is_there():
    """A reviewer asking "why is this in my PR" must have an answer in the plan."""
    plan = plan_package(_m(csproj="a.csproj"), "azure_pipelines")
    assert all(f.get("reason") and f.get("action") in ("create", "refresh")
               for f in plan["files"])


@pytest.mark.unit
def test_everything_excluded_says_why_too():
    plan = plan_package(_m(dockerfile="Dockerfile", helm="chart/Chart.yaml",
                           package_json="p"), "")
    assert all(n.get("reason") for n in plan["not_applicable"])
    assert all(u.get("reason") for u in plan["undecided"])
