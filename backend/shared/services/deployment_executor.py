"""Performing an approved deployment, and reporting what happened — phase 4.

Approval and execution are separate steps on purpose. `deployment_gate` decides whether
something MAY happen; this module makes it happen and records the result.

THREE SEPARATE TRANSACTIONS, deliberately:

    claim   →   call Azure DevOps   →   record

The middle step is a network call to somebody else's service. Holding a database
transaction open across it ties up a pooled connection for as long as ADO takes to
answer, and a handful of concurrent deploys is then enough to starve every other
request in the process.

THE HONEST ANSWER TO A LOST RESPONSE. If the call to ADO raises, we do not know whether
the pipeline started — the request may have been received and the reply lost. There are
two dishonest options: report failure, and someone redeploys on top of a running
deployment; or retry, and it deploys twice. So the outcome is recorded as `error` with
`started_unknown`, the approval stays spent, and the user is told to look in Azure
DevOps before doing anything else. An honest "I do not know" is the only safe answer
here, and it is the one nobody writes unless they have thought about it.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from shared.db import get_db_session_for_tenant
from shared.services import deployment_gate

logger = logging.getLogger(__name__)

#: What each gated action does once approved.
_PERFORMS = {
    "create_pipeline": "create_pipeline",
    "run_pipeline": "run_pipeline",
}


class DeploymentExecutionError(Exception):
    def __init__(self, reason: str, code: str = "failed") -> None:
        super().__init__(reason)
        self.reason = reason
        self.code = code


async def _connector(tenant_id: str, project_id: str):
    from config.connector_factory import get_connector_for_session

    return await get_connector_for_session(
        "azure_pipelines", tenant_id, project_id=project_id, agent_id="deployment",
    )


def _explain(exc: Exception) -> str:
    kind = type(exc).__name__
    if kind == "ConnectorAccessDenied":
        return ("This project has not granted the Deployment agent access to Azure "
                "Pipelines.")
    if kind == "ConnectorNotAvailableError":
        return "Azure DevOps credentials are not configured or not usable."
    return f"The Azure DevOps call failed ({kind})."


async def execute_deployment(*, deployment_id: str, tenant_id: str) -> Dict[str, Any]:
    """Run an approved deployment exactly once, and record what came back.

    Refuses anything not approved, and anything already run — `claim_for_execution` is
    the lock, so two callers racing this produce one deployment and one honest refusal.
    """
    # ── 1. claim ──────────────────────────────────────────────────────────
    async with get_db_session_for_tenant(tenant_id) as db:
        dep = await deployment_gate.claim_for_execution(
            db, deployment_id=deployment_id, tenant_id=tenant_id
        )
        claimed = {
            "action": dep.action, "request": dict(dep.request or {}),
            "project_id": str(dep.project_id), "environment": dep.environment,
        }

    action = claimed["action"]
    request = claimed["request"]
    ado_project = request.get("ado_project") or ""

    if action not in _PERFORMS:
        # direct_apply is gated and recorded but not yet performed. Saying so beats a
        # silent no-op that leaves the row looking deployed.
        await _record(deployment_id, tenant_id, "error", outcome={
            "detail": f"{action!r} is approved but this platform cannot perform it yet.",
        })
        raise DeploymentExecutionError(
            f"{action!r} is approved, but performing it is not implemented. Nothing "
            "has been deployed.",
            code="not_implemented",
        )

    # ── 2. the network call, with no transaction held open ────────────────
    try:
        conn = await _connector(tenant_id, claimed["project_id"])
        if action == "create_pipeline":
            result = await conn.write_adapter(
                "create_pipeline", project=ado_project, name=request.get("name") or "",
                yaml_path=request.get("yaml_path") or "azure-pipelines.yml",
                repository_id=request.get("repository_id") or request.get("repository") or "",
                repository_name=request.get("repository") or "",
            )
            external_id = str(result.get("id") or "")
            external_url = result.get("url") or ""
            status, outcome = "succeeded", {"pipeline": result}
        else:
            result = await conn.write_adapter(
                "run_pipeline", project=ado_project,
                pipeline_id=int(request.get("pipeline_id") or 0),
                branch=request.get("branch") or "",
            )
            external_id = str(result.get("id") or "")
            external_url = result.get("url") or ""
            # A queued run has NOT succeeded. It is running, and calling it a success
            # here is how a deployment that later fails gets recorded as fine.
            status, outcome = "running", {"run": result}
    except Exception as exc:  # noqa: BLE001
        logger.error("deployment %s failed: %s", deployment_id, type(exc).__name__)
        # DID THE REQUEST EVER LEAVE? Only a run can be ambiguous, and only when the
        # call actually went out. A missing credential or an ungranted connector fails
        # BEFORE anything is sent, so nothing can have started — and saying "it might
        # have" there is a false alarm, which is how people learn to skim the warning
        # on the day it is real.
        never_sent = type(exc).__name__ in (
            "ConnectorAccessDenied", "ConnectorNotAvailableError", "ValueError",
        )
        started_unknown = action == "run_pipeline" and not never_sent
        await _record(deployment_id, tenant_id, "error", outcome={
            "detail": _explain(exc),
            "started_unknown": started_unknown,
            "what_to_do": (
                "Check Azure DevOps before retrying. The request may have been "
                "received and only the reply lost, and redeploying on top of a running "
                "deployment is worse than waiting."
            ) if started_unknown else (
                "Nothing was sent, so nothing has been deployed. Fix the cause and "
                "raise a new request."
            ),
        })
        raise DeploymentExecutionError(_explain(exc), code="connector_failed") from None

    # ── 3. record ─────────────────────────────────────────────────────────
    await _record(deployment_id, tenant_id, status, external_id=external_id,
                  external_url=external_url, outcome=outcome)
    return {
        "deployment_id": deployment_id, "action": action, "execution_status": status,
        "external_id": external_id, "external_url": external_url, "outcome": outcome,
    }


async def refresh_status(*, deployment_id: str, tenant_id: str) -> Dict[str, Any]:
    """Re-read a running deployment from Azure DevOps and update what we know.

    A run that has not finished stays `running`. A run that failed is written down
    WITH THE STAGE THAT FAILED — "the deployment failed" makes somebody go and read
    logs this call already has in its hand.
    """
    from sqlalchemy import select

    from shared.models.orm import Deployment

    async with get_db_session_for_tenant(tenant_id) as db:
        dep = (await db.execute(
            select(Deployment).where(
                Deployment.id == deployment_id, Deployment.tenant_id == tenant_id
            )
        )).scalar_one_or_none()
        if dep is None:
            raise DeploymentExecutionError("No such deployment.", code="not_found")
        snapshot = {
            "action": dep.action, "request": dict(dep.request or {}),
            "project_id": str(dep.project_id), "external_id": dep.external_id,
            "execution_status": dep.execution_status,
        }

    if snapshot["execution_status"] in ("succeeded", "failed", "canceled"):
        return {"deployment_id": deployment_id, "unchanged": True, **snapshot}
    if not snapshot["external_id"]:
        return {"deployment_id": deployment_id, "unchanged": True,
                "detail": "This deployment has no run to follow.", **snapshot}

    request = snapshot["request"]
    try:
        conn = await _connector(tenant_id, snapshot["project_id"])
        run = await conn.read_adapter(
            "get_run", project=request.get("ado_project") or "",
            pipeline_id=int(request.get("pipeline_id") or 0),
            run_id=int(snapshot["external_id"]),
        )
        failed_stages = []
        if run.get("result") in ("failed", "canceled", "partially_succeeded"):
            timeline = await conn.read_adapter(
                "get_run_timeline", project=request.get("ado_project") or "",
                run_id=int(snapshot["external_id"]),
            )
            failed_stages = timeline.get("failed", [])
    except Exception as exc:  # noqa: BLE001
        # Could not read it. That is NOT a failed deployment — leaving the status alone
        # is the difference between "we cannot see it" and "it broke".
        return {"deployment_id": deployment_id, "unchanged": True,
                "detail": f"Could not read the run ({type(exc).__name__}). The "
                          "deployment status is unchanged, not failed.",
                **snapshot}

    status = "running" if not run.get("finished") else {
        "succeeded": "succeeded", "failed": "failed", "canceled": "canceled",
        "partially_succeeded": "failed",
    }.get(run.get("result") or "", "error")

    outcome: Dict[str, Any] = {"run": run}
    if failed_stages:
        outcome["failed_stages"] = failed_stages
        outcome["summary"] = "Failed at: " + ", ".join(
            s.get("name") or "?" for s in failed_stages
        )

    await _record(deployment_id, tenant_id, status,
                  external_url=run.get("url") or "", outcome=outcome)
    return {"deployment_id": deployment_id, "execution_status": status,
            "run": run, "failed_stages": failed_stages}


async def _record(
    deployment_id: str, tenant_id: str, status: str, *, external_id: str = "",
    external_url: str = "", outcome: Optional[Dict[str, Any]] = None,
) -> None:
    async with get_db_session_for_tenant(tenant_id) as db:
        await deployment_gate.record_outcome(
            db, deployment_id=deployment_id, tenant_id=tenant_id, status=status,
            external_id=external_id, external_url=external_url, outcome=outcome,
        )
