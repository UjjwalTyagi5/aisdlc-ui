"""The Azure Pipelines connector — PHASE 0 of the deployment agent.

THE BUG THIS FILE EXISTS TO PREVENT. ADO reports `state` and `result` separately, and
`result` is empty until a run finishes. Code that reads `result` alone sees a running
deployment as "not succeeded" and reports a failure that has not happened. Half these
tests are about that one distinction.

The other half are about honesty: a capability that is not there says so, rather than
returning a shape that lets the caller believe it worked.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.connectors.azure_pipelines import (  # noqa: E402
    AzurePipelinesConnector, _canonical_run,
)
from config.connectors.base import ConnectorNotAvailableError  # noqa: E402


@pytest.fixture
def conn(monkeypatch):
    """A connector whose HTTP layer is replaced by a recorder."""
    c = AzurePipelinesConnector("https://dev.azure.com/acme", "tenant-1")
    calls: list[dict] = []
    responses: Dict[str, Any] = {}

    async def _request(method, path, *, project, params=None, json_body=None,
                       api_version="7.1"):
        calls.append({"method": method, "path": path, "project": project,
                      "body": json_body, "api_version": api_version})
        return responses.get(path, {})

    monkeypatch.setattr(c, "_request", _request)
    c._calls, c._responses = calls, responses  # type: ignore[attr-defined]
    return c


# -- state vs result -----------------------------------------------------------


@pytest.mark.unit
def test_a_running_deploy_is_not_reported_as_a_failure():
    """THE POINT OF THE FILE. An in-flight run has no result yet; treating the empty
    result as an outcome tells the user a deploy failed while it is still going."""
    run = _canonical_run({"id": 7, "state": "inProgress"})
    assert run["status"] == "running"
    assert run["result"] is None
    assert run["finished"] is False


@pytest.mark.unit
def test_a_queued_run_is_queued_not_unknown():
    assert _canonical_run({"state": "notStarted"})["status"] == "queued"


@pytest.mark.unit
@pytest.mark.parametrize("raw,expected", [
    ("succeeded", "succeeded"),
    ("failed", "failed"),
    ("canceled", "canceled"),
    ("cancelled", "canceled"),
    ("partiallySucceeded", "partially_succeeded"),
])
def test_a_finished_run_reports_its_outcome(raw, expected):
    run = _canonical_run({"state": "completed", "result": raw})
    assert run["status"] == expected
    assert run["result"] == expected
    assert run["finished"] is True


@pytest.mark.unit
def test_a_partial_success_is_not_flattened_into_success():
    """"Mostly deployed" is a state someone has to act on, not a green tick."""
    assert _canonical_run(
        {"state": "completed", "result": "partiallySucceeded"}
    )["result"] == "partially_succeeded"


@pytest.mark.unit
def test_an_unrecognised_result_is_passed_through_not_swallowed():
    """An ADO result this map has not seen must stay visible. Mapping it to
    "succeeded" or dropping it would hide a real outcome."""
    run = _canonical_run({"state": "completed", "result": "someNewAdoResult"})
    assert run["result"] == "someNewAdoResult"


# -- naming what failed --------------------------------------------------------


@pytest.mark.unit
async def test_the_timeline_singles_out_what_failed(conn):
    """"The pipeline failed" helps nobody. The failing stage is the answer."""
    conn._responses["build/builds/42/timeline"] = {"records": [
        {"name": "Build", "type": "Stage", "state": "completed", "result": "succeeded"},
        {"name": "Deploy to prod", "type": "Stage", "state": "completed",
         "result": "failed", "issues": [{"type": "error", "message": "image pull backoff"}]},
    ]}
    out = await conn.get_run_timeline(project="P", run_id=42)
    assert [r["name"] for r in out["failed"]] == ["Deploy to prod"]
    assert out["failed"][0]["issues"][0]["message"] == "image pull backoff"
    assert len(out["records"]) == 2


@pytest.mark.unit
async def test_a_cancelled_stage_counts_as_something_that_went_wrong(conn):
    conn._responses["build/builds/1/timeline"] = {"records": [
        {"name": "Deploy", "result": "canceled"}]}
    assert (await conn.get_run_timeline(project="P", run_id=1))["failed"]


@pytest.mark.unit
async def test_a_clean_run_has_nothing_in_failed(conn):
    conn._responses["build/builds/1/timeline"] = {"records": [
        {"name": "Build", "result": "succeeded"}]}
    assert (await conn.get_run_timeline(project="P", run_id=1))["failed"] == []


# -- creating and running ------------------------------------------------------


@pytest.mark.unit
async def test_creating_a_pipeline_points_at_a_yaml_path(conn):
    await conn.create_pipeline(project="P", name="deploy-web",
                               yaml_path="azure-pipelines.yml", repository_id="repo-guid")
    body = conn._calls[-1]["body"]
    assert body["configuration"]["type"] == "yaml"
    assert body["configuration"]["repository"]["id"] == "repo-guid"


@pytest.mark.unit
async def test_a_yaml_path_is_normalised_to_a_repo_root_path(conn):
    """ADO rejects a configuration path without the leading slash, and the agent will
    naturally say "azure-pipelines.yml"."""
    await conn.create_pipeline(project="P", name="x", yaml_path="azure-pipelines.yml",
                               repository_id="r")
    assert conn._calls[-1]["body"]["configuration"]["path"] == "/azure-pipelines.yml"


@pytest.mark.unit
async def test_an_already_rooted_path_is_left_alone(conn):
    await conn.create_pipeline(project="P", name="x", yaml_path="/deploy/pipe.yml",
                               repository_id="r")
    assert conn._calls[-1]["body"]["configuration"]["path"] == "/deploy/pipe.yml"


@pytest.mark.unit
async def test_running_a_pipeline_targets_a_full_git_ref(conn):
    """"main" is what a user says; refs/heads/main is what ADO needs. Sending the bare
    name silently runs the default branch instead of the one asked for."""
    await conn.run_pipeline(project="P", pipeline_id=3, branch="main")
    ref = conn._calls[-1]["body"]["resources"]["repositories"]["self"]["refName"]
    assert ref == "refs/heads/main"


@pytest.mark.unit
async def test_a_ref_that_is_already_a_ref_is_not_double_prefixed(conn):
    await conn.run_pipeline(project="P", pipeline_id=3, branch="refs/heads/release/1.2")
    assert conn._calls[-1]["body"]["resources"]["repositories"]["self"]["refName"] \
        == "refs/heads/release/1.2"


@pytest.mark.unit
async def test_no_branch_means_no_ref_override_not_an_empty_one(conn):
    """An empty refName is not "the default branch" to ADO — it is a bad request."""
    await conn.run_pipeline(project="P", pipeline_id=3)
    assert "resources" not in (conn._calls[-1]["body"] or {})


@pytest.mark.unit
async def test_run_variables_are_never_marked_secret_by_accident(conn):
    out_body = None
    await conn.run_pipeline(project="P", pipeline_id=3, variables={"env": "prod"})
    out_body = conn._calls[-1]["body"]
    assert out_body["variables"]["env"] == {"value": "prod", "isSecret": False}


# -- honesty -------------------------------------------------------------------


@pytest.mark.unit
async def test_it_refuses_webhooks_rather_than_faking_an_acknowledgement():
    """Declared not_supported. A cheerful 200 here would let someone wire up a service
    hook and believe run events were arriving."""
    c = AzurePipelinesConnector("https://dev.azure.com/acme", "t")
    with pytest.raises(ConnectorNotAvailableError):
        await c.webhook_receiver({"id": "evt"})


@pytest.mark.unit
def test_the_manifest_admits_what_it_cannot_do():
    m = AzurePipelinesConnector().capability_manifest()
    assert m.listen_capabilities["pipeline_run"].status == "not_supported"
    assert m.read_capabilities["download_run_logs"].status == "not_supported"
    assert m.write_capabilities["update_pipeline_yaml"].status == "not_supported"


@pytest.mark.unit
def test_the_deploying_call_is_declared_implemented():
    m = AzurePipelinesConnector().capability_manifest()
    for op in ("create_pipeline", "run_pipeline"):
        assert m.write_capabilities[op].status == "implemented"


@pytest.mark.unit
async def test_credentials_are_per_tenant():
    """A connector built without a tenant must not fall back to anyone's PAT."""
    with pytest.raises(ValueError, match="tenant_id is required"):
        await AzurePipelinesConnector("https://dev.azure.com/acme").auth_adapter()


@pytest.mark.unit
async def test_health_check_never_raises(monkeypatch):
    """A health_check that raises is dropped from the cache, and /connectors/health
    then re-probes inline on EVERY request. Explicitly warned about in
    config/connectors/router.py."""
    c = AzurePipelinesConnector("https://dev.azure.com/acme", "t")

    async def _boom():
        raise RuntimeError("network on fire")

    monkeypatch.setattr(c, "_probe", _boom)
    health = await c.health_check()
    assert health.status == "unhealthy"
    assert health.error == "RuntimeError"


@pytest.mark.unit
async def test_health_errors_never_carry_the_credential(monkeypatch):
    """str(exc) on an auth failure can contain the PAT. Only the type is reported."""
    c = AzurePipelinesConnector("https://dev.azure.com/acme", "t")

    async def _boom():
        raise RuntimeError("401 using pat=super-secret-token")

    monkeypatch.setattr(c, "_probe", _boom)
    assert "super-secret-token" not in str((await c.health_check()).model_dump())


@pytest.mark.unit
async def test_a_call_without_a_project_is_refused(monkeypatch):
    """Pipelines are project-scoped. A blank project builds a URL that quietly hits
    the wrong endpoint."""
    c = AzurePipelinesConnector("https://dev.azure.com/acme", "t")

    async def _auth(tenant_id=""):
        return {"org_url": "https://dev.azure.com/acme", "pat": "p"}

    monkeypatch.setattr(c, "auth_adapter", _auth)
    with pytest.raises(ValueError, match="project is required"):
        await c._request("GET", "pipelines", project="")


@pytest.mark.unit
async def test_missing_credentials_are_named_not_treated_as_empty_results(monkeypatch):
    c = AzurePipelinesConnector("", "t")

    async def _auth(tenant_id=""):
        return {"org_url": "", "pat": ""}

    monkeypatch.setattr(c, "auth_adapter", _auth)
    with pytest.raises(ConnectorNotAvailableError):
        await c._request("GET", "pipelines", project="P")


# -- dispatch ------------------------------------------------------------------


@pytest.mark.unit
async def test_every_declared_operation_is_reachable_through_the_adapters():
    """A capability declared "implemented" that the adapter cannot dispatch is a lie
    the manifest tells."""
    c = AzurePipelinesConnector("https://dev.azure.com/acme", "t")
    m = c.capability_manifest()
    reads = {k for k, v in m.read_capabilities.items() if v.status == "implemented"}
    writes = {k for k, v in m.write_capabilities.items() if v.status == "implemented"}
    for op in reads:
        with pytest.raises((TypeError, ValueError)) as e:
            await c.read_adapter(op)  # missing kwargs, but must not be "unknown"
        assert "Unknown read operation" not in str(e.value)
    for op in writes:
        with pytest.raises((TypeError, ValueError)) as e:
            await c.write_adapter(op)
        assert "Unknown write operation" not in str(e.value)


@pytest.mark.unit
async def test_an_unknown_operation_is_an_error_not_a_silent_none():
    c = AzurePipelinesConnector("https://dev.azure.com/acme", "t")
    with pytest.raises(ValueError, match="Unknown read operation"):
        await c.read_adapter("list_everything")
    with pytest.raises(ValueError, match="Unknown write operation"):
        await c.write_adapter("delete_the_org")


@pytest.mark.unit
async def test_service_connections_come_back_named(conn):
    """Generating a pipeline against a service connection the project does not have
    produces YAML that fails on its first run."""
    conn._responses["serviceendpoint/endpoints"] = {"value": [
        {"id": "sc-1", "name": "prod-aks", "type": "kubernetes"}]}
    out = await conn.list_service_connections(project="P")
    assert out == [{"id": "sc-1", "name": "prod-aks", "type": "kubernetes"}]


@pytest.mark.unit
async def test_service_connections_use_the_preview_api_they_require(conn):
    await conn.list_service_connections(project="P")
    assert conn._calls[-1]["api_version"] == "7.1-preview.4"
