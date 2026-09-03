"""Performing an approved deployment — PHASE 4.

THE HARD CASE, and most of this file. When the call to Azure DevOps raises, we do not
know whether the pipeline started: the request may have been received and only the
reply lost. Reporting failure makes someone redeploy on top of a running deployment.
Retrying deploys twice. The only safe answer is "I do not know, go and look", and it is
the one nobody writes unless they have thought about it.

The rest is the same distinction the connector draws: a QUEUED run has not succeeded,
and a run nobody could read has not failed.
"""
from __future__ import annotations

import sys
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.services import deployment_executor as ex  # noqa: E402
from shared.services.deployment_executor import DeploymentExecutionError  # noqa: E402
from shared.services.deployment_gate import DeploymentGateError  # noqa: E402

TENANT = str(uuid.uuid4())
PROJECT = str(uuid.uuid4())


class _Dep:
    def __init__(self, **kw):
        self.id = kw.get("id", uuid.uuid4())
        self.action = kw.get("action", "run_pipeline")
        self.project_id = PROJECT
        self.environment = "prod"
        self.request = kw.get("request", {
            "pipeline_id": 12, "branch": "main", "ado_project": "AcmeProject"})
        self.external_id = kw.get("external_id")
        self.execution_status = kw.get("execution_status", "not_started")


@pytest.fixture
def world(monkeypatch):
    """Claim, connector and record all replaced by recorders."""
    state: Dict[str, Any] = {
        "dep": _Dep(), "claimed": 0, "claim_error": None,
        "recorded": [], "calls": [], "responses": {}, "raises": None,
    }

    class _Session:
        """Only what the executor asks of it: a SELECT that finds the deployment."""

        async def execute(self, _stmt):
            class _R:
                @staticmethod
                def scalar_one_or_none():
                    return state["dep"]
            return _R()

    @asynccontextmanager
    async def _session(_tenant):
        yield _Session()

    monkeypatch.setattr(ex, "get_db_session_for_tenant", _session)

    async def _claim(_db, *, deployment_id, tenant_id):
        if state["claim_error"]:
            raise state["claim_error"]
        state["claimed"] += 1
        return state["dep"]

    monkeypatch.setattr(ex.deployment_gate, "claim_for_execution", _claim)

    async def _record(_db, *, deployment_id, tenant_id, status, external_id="",
                      external_url="", outcome=None):
        state["recorded"].append({"status": status, "external_id": external_id,
                                  "external_url": external_url, "outcome": outcome})

    monkeypatch.setattr(ex.deployment_gate, "record_outcome", _record)

    class _Conn:
        async def write_adapter(self, op, **kw):
            state["calls"].append({"mode": "write", "op": op, **kw})
            if state["raises"]:
                raise state["raises"]
            return state["responses"].get(op, {})

        async def read_adapter(self, op, **kw):
            state["calls"].append({"mode": "read", "op": op, **kw})
            if state["raises"]:
                raise state["raises"]
            return state["responses"].get(op, {})

    async def _conn(_t, _p):
        if state["raises"] and state.get("raise_on_connect"):
            raise state["raises"]
        return _Conn()

    monkeypatch.setattr(ex, "_connector", _conn)
    return state


def _last(state) -> Dict[str, Any]:
    return state["recorded"][-1]


# -- a queued run is not a success --------------------------------------------


@pytest.mark.unit
async def test_a_queued_run_is_recorded_as_running_not_succeeded(world):
    """Calling a just-started deployment a success is how one that later fails gets
    written down as fine."""
    world["responses"]["run_pipeline"] = {"id": 4417, "status": "queued",
                                          "url": "https://dev.azure.com/x/_build/4417"}
    out = await ex.execute_deployment(deployment_id=str(world["dep"].id),
                                      tenant_id=TENANT)
    assert out["execution_status"] == "running"
    assert _last(world)["status"] == "running"


@pytest.mark.unit
async def test_the_run_it_started_is_recorded_so_it_can_be_followed(world):
    world["responses"]["run_pipeline"] = {"id": 4417,
                                          "url": "https://dev.azure.com/x/_build/4417"}
    out = await ex.execute_deployment(deployment_id=str(world["dep"].id),
                                      tenant_id=TENANT)
    assert out["external_id"] == "4417"
    assert "4417" in out["external_url"]


@pytest.mark.unit
async def test_it_runs_the_branch_that_was_approved(world):
    """The approver read a branch. Running a different one is a deployment nobody
    agreed to."""
    world["dep"].request["branch"] = "release/2.1"
    await ex.execute_deployment(deployment_id=str(world["dep"].id), tenant_id=TENANT)
    assert world["calls"][0]["branch"] == "release/2.1"
    assert world["calls"][0]["pipeline_id"] == 12


@pytest.mark.unit
async def test_creating_a_pipeline_is_a_success_immediately(world):
    """Unlike a run, creation finishes when the call returns."""
    world["dep"] = _Dep(action="create_pipeline",
                        request={"name": "deploy-web", "yaml_path": "azure-pipelines.yml",
                                 "ado_project": "AcmeProject", "repository": "acme-web"})
    world["responses"]["create_pipeline"] = {"id": 9, "name": "deploy-web"}
    out = await ex.execute_deployment(deployment_id=str(world["dep"].id),
                                      tenant_id=TENANT)
    assert out["execution_status"] == "succeeded"


# -- the lost response ---------------------------------------------------------


@pytest.mark.unit
async def test_a_failed_call_does_not_claim_the_deployment_failed(world):
    """THE HARD CASE. The request may have been received and the reply lost."""
    world["raises"] = RuntimeError("connection reset")
    with pytest.raises(DeploymentExecutionError):
        await ex.execute_deployment(deployment_id=str(world["dep"].id),
                                    tenant_id=TENANT)
    outcome = _last(world)["outcome"]
    assert _last(world)["status"] == "error"
    assert outcome["started_unknown"] is True


@pytest.mark.unit
async def test_it_says_to_look_before_retrying(world):
    """Redeploying on top of a running deployment is worse than waiting."""
    world["raises"] = RuntimeError("timeout")
    with pytest.raises(DeploymentExecutionError):
        await ex.execute_deployment(deployment_id=str(world["dep"].id),
                                    tenant_id=TENANT)
    assert "Check Azure DevOps" in _last(world)["outcome"]["what_to_do"]


@pytest.mark.unit
async def test_a_failed_pipeline_CREATION_is_not_ambiguous(world):
    """Nothing was deployed, so there is nothing to go and look at. Saying "unknown"
    here would be false caution."""
    world["dep"] = _Dep(action="create_pipeline",
                        request={"name": "x", "ado_project": "P"})
    world["raises"] = RuntimeError("boom")
    with pytest.raises(DeploymentExecutionError):
        await ex.execute_deployment(deployment_id=str(world["dep"].id),
                                    tenant_id=TENANT)
    assert _last(world)["outcome"]["started_unknown"] is False


@pytest.mark.unit
async def test_a_missing_grant_is_named_rather_than_called_a_deploy_failure(world):
    from config.connectors.scoped import ConnectorAccessDenied

    world["raises"] = ConnectorAccessDenied("azure_pipelines", "write", None)
    world["raise_on_connect"] = True
    with pytest.raises(DeploymentExecutionError, match="has not granted"):
        await ex.execute_deployment(deployment_id=str(world["dep"].id),
                                    tenant_id=TENANT)


# -- the gate still governs ----------------------------------------------------


@pytest.mark.unit
async def test_an_unapproved_deployment_never_reaches_the_connector(world):
    world["claim_error"] = DeploymentGateError("not approved", code="not_approved")
    with pytest.raises(DeploymentGateError):
        await ex.execute_deployment(deployment_id=str(world["dep"].id),
                                    tenant_id=TENANT)
    assert world["calls"] == []


@pytest.mark.unit
async def test_an_already_run_deployment_never_reaches_the_connector(world):
    world["claim_error"] = DeploymentGateError("already ran", code="already_executed")
    with pytest.raises(DeploymentGateError):
        await ex.execute_deployment(deployment_id=str(world["dep"].id),
                                    tenant_id=TENANT)
    assert world["calls"] == []


@pytest.mark.unit
async def test_an_action_that_cannot_be_performed_says_so_instead_of_no_opping(world):
    """A silent no-op leaves the row looking deployed."""
    world["dep"] = _Dep(action="direct_apply")
    with pytest.raises(DeploymentExecutionError, match="not implemented") as e:
        await ex.execute_deployment(deployment_id=str(world["dep"].id),
                                    tenant_id=TENANT)
    assert e.value.code == "not_implemented"
    assert _last(world)["status"] == "error"
    assert world["calls"] == []


# -- refreshing ----------------------------------------------------------------


@pytest.mark.unit
async def test_a_finished_run_is_written_down_with_the_stage_that_failed(world):
    """"The deployment failed" makes someone go and read logs this call already had."""
    world["dep"] = _Dep(external_id="4417", execution_status="running")
    world["responses"]["get_run"] = {"id": 4417, "finished": True, "result": "failed"}
    world["responses"]["get_run_timeline"] = {"failed": [
        {"name": "Deploy to prod", "result": "failed"}]}
    out = await ex.refresh_status(deployment_id=str(world["dep"].id), tenant_id=TENANT)
    assert out["execution_status"] == "failed"
    assert out["failed_stages"][0]["name"] == "Deploy to prod"
    assert "Failed at: Deploy to prod" in _last(world)["outcome"]["summary"]


@pytest.mark.unit
async def test_a_run_still_going_stays_running(world):
    world["dep"] = _Dep(external_id="4417", execution_status="running")
    world["responses"]["get_run"] = {"id": 4417, "finished": False, "result": None}
    out = await ex.refresh_status(deployment_id=str(world["dep"].id), tenant_id=TENANT)
    assert out["execution_status"] == "running"


@pytest.mark.unit
async def test_a_partial_success_is_treated_as_a_failure_to_look_at(world):
    """"Mostly deployed" is something somebody has to act on."""
    world["dep"] = _Dep(external_id="1", execution_status="running")
    world["responses"]["get_run"] = {"finished": True, "result": "partially_succeeded"}
    world["responses"]["get_run_timeline"] = {"failed": [{"name": "Smoke tests"}]}
    out = await ex.refresh_status(deployment_id=str(world["dep"].id), tenant_id=TENANT)
    assert out["execution_status"] == "failed"


@pytest.mark.unit
async def test_a_run_that_cannot_be_read_is_not_a_failed_deployment(world):
    """"We cannot see it" and "it broke" are different sentences. Recording the second
    when the first is true sends someone rolling back a healthy deploy."""
    world["dep"] = _Dep(external_id="4417", execution_status="running")
    world["raises"] = RuntimeError("ADO unreachable")
    out = await ex.refresh_status(deployment_id=str(world["dep"].id), tenant_id=TENANT)
    assert out["unchanged"] is True
    assert "not failed" in out["detail"]
    assert world["recorded"] == []


@pytest.mark.unit
async def test_a_settled_deployment_is_not_re_read(world):
    world["dep"] = _Dep(external_id="4417", execution_status="succeeded")
    out = await ex.refresh_status(deployment_id=str(world["dep"].id), tenant_id=TENANT)
    assert out["unchanged"] is True
    assert world["calls"] == []


@pytest.mark.unit
async def test_a_deployment_with_no_run_has_nothing_to_follow(world):
    world["dep"] = _Dep(external_id=None, execution_status="running")
    out = await ex.refresh_status(deployment_id=str(world["dep"].id), tenant_id=TENANT)
    assert out["unchanged"] is True
    assert "no run to follow" in out["detail"]


# -- the ambiguity is real only when the call went out -------------------------


@pytest.mark.unit
async def test_a_missing_credential_is_not_ambiguous(world):
    """It fails BEFORE anything is sent, so nothing can have started. Warning that it
    might have is a false alarm, and false alarms are how people learn to skim the
    warning on the day it is real.

    Found by running the whole flow against a real project with no ADO credential —
    the honest-degradation path fired correctly and then over-warned.
    """
    from config.connectors.base import ConnectorNotAvailableError

    world["raises"] = ConnectorNotAvailableError("no pat")
    world["raise_on_connect"] = True
    with pytest.raises(DeploymentExecutionError):
        await ex.execute_deployment(deployment_id=str(world["dep"].id), tenant_id=TENANT)
    outcome = _last(world)["outcome"]
    assert outcome["started_unknown"] is False
    assert "Nothing was sent" in outcome["what_to_do"]


@pytest.mark.unit
async def test_an_ungranted_connector_is_not_ambiguous_either(world):
    from config.connectors.scoped import ConnectorAccessDenied

    world["raises"] = ConnectorAccessDenied("azure_pipelines", "write", None)
    world["raise_on_connect"] = True
    with pytest.raises(DeploymentExecutionError):
        await ex.execute_deployment(deployment_id=str(world["dep"].id), tenant_id=TENANT)
    assert _last(world)["outcome"]["started_unknown"] is False


@pytest.mark.unit
async def test_a_call_that_really_went_out_is_still_ambiguous(world):
    """The case the flag exists for: the request may have been received and the reply
    lost. This must NOT be narrowed away by the fix above."""
    world["raises"] = TimeoutError("read timeout")
    with pytest.raises(DeploymentExecutionError):
        await ex.execute_deployment(deployment_id=str(world["dep"].id), tenant_id=TENANT)
    assert _last(world)["outcome"]["started_unknown"] is True
