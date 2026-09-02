"""Pipeline lifecycle tools — PHASE 3.

THE ONE THING THAT MUST BE TRUE: the two request_* tools file an approval and perform
nothing. An agent that says "I've started the deployment" when it has queued an approval
is worse than one that refuses outright, because nobody goes looking for an approval
they were told had already happened.

So these tests check what the tools DID NOT do as much as what they returned.
"""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents_orchestrator.deployment_agent.config.session_state import get_session  # noqa: E402
from agents_orchestrator.deployment_agent.tools import pipeline_tools as pt  # noqa: E402
from config.ws_helper import set_session_id, set_user_id  # noqa: E402

TENANT = str(uuid.uuid4())
PROJECT = str(uuid.uuid4())


@pytest.fixture
def session(monkeypatch):
    """A bound deployment session, with the connector and DB replaced by recorders."""
    sid = f"pt-{uuid.uuid4().hex[:8]}"
    set_session_id(sid)
    set_user_id("alice")
    s = get_session(sid)
    s.tenant_id, s.project_id = TENANT, PROJECT
    s.ado_project, s.repo_name = "AcmeProject", "acme-web"
    s.environment, s.deploy_via = "prod", "azure_pipelines"
    s.source_branch = "main"

    calls: List[Dict[str, Any]] = []
    responses: Dict[str, Any] = {}

    class _Conn:
        async def read_adapter(self, op, **kw):
            calls.append({"mode": "read", "op": op, **kw})
            return responses.get(op, [])

        async def write_adapter(self, op, **kw):
            calls.append({"mode": "write", "op": op, **kw})
            return responses.get(op, {})

    async def _conn(_ctx):
        return _Conn()

    monkeypatch.setattr(pt, "_pipelines_connector", _conn)

    filed: List[Dict[str, Any]] = []

    async def _file_request(action, request, ctx):
        filed.append({"action": action, "request": request, "ctx": ctx})
        return json.dumps({
            "status": "awaiting_approval", "deployment_id": "dep-1",
            "action": action, "environment": ctx["environment"],
            "request": request, "nothing_has_run": True,
            "detail": "NOTHING HAS HAPPENED YET.",
        })

    monkeypatch.setattr(pt, "_file_request", _file_request)
    return {"state": s, "calls": calls, "responses": responses, "filed": filed}


# -- the gated tools perform nothing -------------------------------------------


@pytest.mark.unit
async def test_asking_to_run_a_pipeline_does_not_run_it(session):
    """THE POINT OF THE PHASE. Not one write reaches the connector."""
    out = json.loads(await pt.request_pipeline_run.ainvoke({"pipeline_id": 12}))
    assert out["status"] == "awaiting_approval"
    assert out["nothing_has_run"] is True
    assert [c for c in session["calls"] if c["mode"] == "write"] == []


@pytest.mark.unit
async def test_asking_to_create_a_pipeline_does_not_create_it(session):
    out = json.loads(await pt.request_pipeline_creation.ainvoke({"name": "deploy-web"}))
    assert out["status"] == "awaiting_approval"
    assert [c for c in session["calls"] if c["mode"] == "write"] == []


@pytest.mark.unit
async def test_the_answer_forbids_the_words_that_would_mislead(session):
    """"queued", "started", "in progress" are all false, and all things a model
    reaches for when it has just done something that felt like work."""
    out = json.loads(await pt.request_pipeline_run.ainvoke({"pipeline_id": 12}))
    assert "NOTHING HAS HAPPENED YET" in out["detail"]


@pytest.mark.unit
async def test_a_run_request_records_what_would_run(session):
    """The approver reads this. A request that does not say which branch is one
    nobody can meaningfully approve."""
    await pt.request_pipeline_run.ainvoke({"pipeline_id": 12, "branch": "release/2.1"})
    req = session["filed"][0]["request"]
    assert req["pipeline_id"] == 12
    assert req["branch"] == "release/2.1"


@pytest.mark.unit
async def test_a_run_request_falls_back_to_the_prepared_branch(session):
    await pt.request_pipeline_run.ainvoke({"pipeline_id": 12})
    assert session["filed"][0]["request"]["branch"] == "main"


@pytest.mark.unit
async def test_a_creation_request_records_the_yaml_path_and_repo(session):
    await pt.request_pipeline_creation.ainvoke(
        {"name": "deploy-web", "yaml_path": "ci/azure-pipelines.yml"})
    req = session["filed"][0]["request"]
    assert req["yaml_path"] == "ci/azure-pipelines.yml"
    assert req["repository"] == "acme-web"


@pytest.mark.unit
async def test_a_pipeline_needs_a_name(session):
    out = json.loads(await pt.request_pipeline_creation.ainvoke({"name": ""}))
    assert out["error"] == "no_name"
    assert session["filed"] == []


# -- what cannot be driven says so ---------------------------------------------


@pytest.mark.unit
async def test_it_does_not_pretend_to_drive_jenkins(session):
    """The platform writes a Jenkinsfile and holds no Jenkins credential. Implying a
    build will run is the confident wrongness this whole module avoids."""
    session["state"].deploy_via = "jenkins"
    out = json.loads(await pt.request_pipeline_run.ainvoke({"pipeline_id": 1}))
    assert out["error"] == "not_drivable"
    assert "no Jenkins credential" in out["detail"]
    assert session["filed"] == []


@pytest.mark.unit
async def test_github_actions_is_explained_rather_than_half_supported(session):
    """A workflow triggers from the file in the repo. There is no pipeline to create."""
    session["state"].deploy_via = "github_actions"
    out = json.loads(await pt.request_pipeline_creation.ainvoke({"name": "x"}))
    assert out["error"] == "not_drivable"
    assert "workflow" in out["detail"].lower()


@pytest.mark.unit
async def test_an_unbound_connector_is_named_as_the_problem(session):
    session["state"].deploy_via = "unknown"
    out = json.loads(await pt.list_pipelines.ainvoke({}))
    assert out["error"] == "no_connector"


# -- reads ---------------------------------------------------------------------


@pytest.mark.unit
async def test_pipelines_are_listed_from_the_bound_ado_project(session):
    session["responses"]["list_pipelines"] = [{"id": 3, "name": "deploy-web"}]
    out = json.loads(await pt.list_pipelines.ainvoke({}))
    assert out["project"] == "AcmeProject"
    assert out["pipelines"][0]["name"] == "deploy-web"


@pytest.mark.unit
async def test_a_failed_run_names_the_stage_that_broke(session):
    """"The pipeline failed" sends someone to read logs the platform already had."""
    session["responses"]["get_run"] = {"id": 44, "state": "completed",
                                       "result": "failed", "finished": True}
    session["responses"]["get_run_timeline"] = {
        "records": [], "failed": [{"name": "Deploy to prod", "result": "failed",
                                   "issues": [{"message": "image pull backoff"}]}]}
    out = json.loads(await pt.get_run_status.ainvoke({"pipeline_id": 3, "run_id": 44}))
    assert out["failed_stages"][0]["name"] == "Deploy to prod"


@pytest.mark.unit
async def test_a_running_deploy_does_not_trigger_a_failure_investigation(session):
    """An unfinished run has result None. Fetching the timeline for it would report
    a failure that has not happened."""
    session["responses"]["get_run"] = {"id": 44, "state": "inProgress",
                                       "result": None, "status": "running"}
    out = json.loads(await pt.get_run_status.ainvoke({"pipeline_id": 3, "run_id": 44}))
    assert "failed_stages" not in out
    assert not any(c["op"] == "get_run_timeline" for c in session["calls"])


@pytest.mark.unit
async def test_a_successful_run_is_not_investigated_either(session):
    session["responses"]["get_run"] = {"id": 44, "state": "completed",
                                       "result": "succeeded", "finished": True}
    out = json.loads(await pt.get_run_status.ainvoke({"pipeline_id": 3, "run_id": 44}))
    assert "failed_stages" not in out


@pytest.mark.unit
async def test_no_service_connections_is_reported_as_a_blocker_not_an_empty_list(session):
    """An empty list looks like "nothing to worry about". It means the pipeline has no
    way to reach a cluster."""
    session["responses"]["list_service_connections"] = []
    out = json.loads(await pt.list_service_connections.ainvoke({}))
    assert "has to be created in Azure DevOps" in out["detail"]


@pytest.mark.unit
async def test_service_connections_are_returned_when_they_exist(session):
    session["responses"]["list_service_connections"] = [
        {"id": "sc-1", "name": "prod-aks", "type": "kubernetes"}]
    out = json.loads(await pt.list_service_connections.ainvoke({}))
    assert out["service_connections"][0]["name"] == "prod-aks"


# -- connector problems are told apart -----------------------------------------


@pytest.mark.unit
async def test_a_missing_grant_is_not_reported_as_missing_credentials(monkeypatch,
                                                                     session):
    """Different fixes. Telling someone to reconnect Azure DevOps when the real
    problem is an ungranted agent wastes the one message they read."""
    from config.connectors.scoped import ConnectorAccessDenied

    async def _boom(_ctx):
        raise ConnectorAccessDenied("azure_pipelines", "read", None)

    monkeypatch.setattr(pt, "_pipelines_connector", _boom)
    out = json.loads(await pt.list_pipelines.ainvoke({}))
    assert out["error"] == "ConnectorAccessDenied"
    assert "has not granted" in out["detail"]


@pytest.mark.unit
async def test_a_missing_credential_says_so(monkeypatch, session):
    from config.connectors.base import ConnectorNotAvailableError

    async def _boom(_ctx):
        raise ConnectorNotAvailableError("no pat")

    monkeypatch.setattr(pt, "_pipelines_connector", _boom)
    out = json.loads(await pt.list_pipelines.ainvoke({}))
    assert "not configured or not usable" in out["detail"]


# -- following a request -------------------------------------------------------


@pytest.mark.unit
async def test_a_synthesised_deployment_id_is_a_clean_not_found(session):
    out = json.loads(await pt.check_deployment_request.ainvoke(
        {"deployment_id": "the-one-from-earlier"}))
    assert out["error"] == "not_found"
