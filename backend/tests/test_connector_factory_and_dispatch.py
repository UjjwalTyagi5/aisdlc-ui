"""Regression tests for the connector factory and the deployment provider dispatcher.

Two things are pinned here:
  1. Every registered connector can actually be constructed by the factory. Slack and
     Azure Repos could not — the factory calls connector_class(org_url=..., ...) but
     SlackConnector took only bot_token and AzureReposConnector required a positional
     org_url, so get_connector_for_session raised TypeError for them.
  2. Adding GitHub Actions to the deployment agent did not change the Azure DevOps
     path, which is the default and by far the more travelled one.
"""
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.connector_factory import (  # noqa: E402
    _CONNECTOR_REGISTRY,
    get_connector_for_session,
    list_available_connectors,
)
from config.connectors.base import BaseConnector  # noqa: E402
from config.connectors.router import _EXPECTED_CONNECTOR_NAMES  # noqa: E402

# The kinds the catalogue advertises as connectable, mapped to their internal
# connector_name. github/github_issues is the one place the two differ.
_EXPECTED_INTERNAL_NAMES = {"github": "github_issues"}


@pytest.mark.unit
@pytest.mark.parametrize("kind", sorted(_CONNECTOR_REGISTRY))
async def test_every_registered_kind_constructs(kind):
    """The generic factory tail must work for every kind, not just the ADO-shaped ones."""
    connector = await get_connector_for_session(kind=kind, tenant_id="t-1")
    assert isinstance(connector, BaseConnector)
    assert connector.connector_name
    assert connector.display_name
    # tenant_id is run context and must reach the instance, or credential resolution
    # silently falls back to the global/env tier.
    assert getattr(connector, "_tenant_id", None) == "t-1"


@pytest.mark.unit
@pytest.mark.parametrize("kind", ["slack", "azure_repos"])
async def test_previously_broken_kinds_no_longer_raise(kind):
    """Both of these raised TypeError from the factory before the constructors were aligned."""
    assert await get_connector_for_session(kind=kind, tenant_id="t-1") is not None


@pytest.mark.unit
async def test_new_kinds_are_registered():
    kinds = {m["kind"] for m in await list_available_connectors()}
    assert {"github_actions", "ms_teams", "sharepoint"} <= kinds


@pytest.mark.unit
async def test_health_probe_covers_every_expected_name():
    """_EXPECTED_CONNECTOR_NAMES must be satisfiable by the probe list.

    If a name there is never produced, GET /connectors/health fails its subset test and
    re-probes inline on EVERY request — a permanent latency regression that no test
    other than this one would notice.
    """
    from process_api import _build_connectors_for_health_probe

    probed = {c.connector_name for c in _build_connectors_for_health_probe()}
    missing = _EXPECTED_CONNECTOR_NAMES - probed
    assert not missing, f"expected in health cache but never probed: {sorted(missing)}"


@pytest.mark.unit
async def test_factory_requires_a_tenant():
    with pytest.raises(ValueError, match="tenant_id is required"):
        await get_connector_for_session(kind="github_actions", tenant_id="")


# ── Deployment provider dispatch ──────────────────────────────────────────────


@pytest.mark.unit
def test_ado_step_plan_uses_the_dispatcher():
    from agents_orchestrator.deployment_agent.deployment_agent_api import _build_step_plan

    names = [t.name for t in _build_step_plan("ado_deploy")]
    assert "trigger_deployment_pipeline" in names
    # The raw ADO trigger now sits behind the dispatcher rather than in the plan.
    assert "trigger_azure_pipeline" not in names


@pytest.mark.unit
@pytest.mark.parametrize(
    "state,expected",
    [
        ({}, "azure"),                                              # default unchanged
        ({"deploy_via": ""}, "azure"),
        ({"deploy_via": "azure_pipelines"}, "azure"),
        ({"deploy_via": "github_actions"}, "gha"),
        ({"deployment_request": {"deploy_via": "github_actions"}}, "gha"),
    ],
)
async def test_dispatcher_routes_by_deploy_via(state, expected):
    """Absent deploy_via MUST still mean Azure Pipelines — every existing run relies on it."""
    import agents_orchestrator.deployment_agent.agents.pipeline_app as pa

    calls = []

    class _Stub:
        def __init__(self, tag):
            self.tag = tag

        async def ainvoke(self, payload):
            calls.append(self.tag)
            return payload["state"]

    with patch.object(pa, "trigger_azure_pipeline", _Stub("azure")), patch.object(
        pa, "trigger_github_actions_workflow", _Stub("gha")
    ):
        await pa.trigger_deployment_pipeline.ainvoke({"state": dict(state)})

    assert calls == [expected]


@pytest.mark.unit
async def test_argocd_needs_no_trigger():
    """GitOps: the pushed manifests ARE the deployment. Recorded, not silently skipped."""
    import agents_orchestrator.deployment_agent.agents.pipeline_app as pa

    result = await pa.trigger_deployment_pipeline.ainvoke({"state": {"deploy_via": "argocd"}})
    assert result["pipeline_provider"] == "argocd"
    assert result["pipeline_trigger_status"] == "triggered"


@pytest.mark.unit
async def test_github_actions_trigger_without_a_repo_fails_clearly():
    import agents_orchestrator.deployment_agent.agents.pipeline_app as pa

    result = await pa.trigger_github_actions_workflow.ainvoke({"state": {}})
    assert result["pipeline_trigger_status"] == "failed"
    assert any("gha_repo" in e for e in result.get("errors", []))
