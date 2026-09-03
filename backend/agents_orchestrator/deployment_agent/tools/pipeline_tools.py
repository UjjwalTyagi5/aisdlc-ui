"""Pipeline lifecycle tools — phase 3.

Reading a pipeline is free. Creating one and running one change something outside the
platform, so those two do NOT do the thing: they file a request through
`shared.services.deployment_gate` and hand back an id that is waiting for a human.

THE FAILURE MODE THIS IS SHAPED AROUND. An agent that says "I've started the
deployment" when it has actually queued an approval is worse than one that refuses
outright, because nobody goes looking for the approval. Every gated tool here returns
the word `awaiting_approval`, the id, and who has to act — and says plainly that
nothing has run.

WHY THE READS MATTER TOO. `get_run_status` pulls the timeline on a failure so the
answer names the stage that broke. "The pipeline failed" sends someone to go and read
logs the platform already had.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from langchain_core.tools import tool

from agents_orchestrator.deployment_agent.config.session_state import get_session
from config.ws_helper import get_session_id, get_user_id

logger = logging.getLogger(__name__)

#: The CI systems this agent can drive. A Jenkinsfile is a file the agent writes; the
#: platform holds no Jenkins credential and starts no Jenkins job, and saying otherwise
#: would be the exact kind of confident wrongness this module exists to avoid.
_DRIVABLE = ("azure_pipelines",)


def _ctx() -> Dict[str, Any]:
    s = get_session(get_session_id())
    return {
        "tenant_id": s.tenant_id or "",
        "project_id": s.project_id or "",
        # Whose Azure DevOps credential this conversation authenticates with.
        "user_id": str(get_user_id() or ""),
        "ado_project": s.ado_project or s.repo_name or "",
        "repo_name": s.repo_name or "",
        "environment": s.environment or "",
        "branch": s.source_branch or "",
        "deploy_via": (s.deploy_via or "").strip().lower(),
    }


def _unsupported(via: str) -> Optional[str]:
    """Why this connector cannot be driven, or None when it can."""
    if via in _DRIVABLE:
        return None
    if via == "github_actions":
        return json.dumps({
            "error": "not_drivable",
            "detail": "GitHub Actions runs from the workflow file in the repo. Merge "
                      "the deployment PR and the workflow triggers itself; there is no "
                      "pipeline to create here.",
        }, indent=2)
    if via == "jenkins":
        return json.dumps({
            "error": "not_drivable",
            "detail": "The platform writes a Jenkinsfile but holds no Jenkins "
                      "credential and cannot create or start a Jenkins job. Say so "
                      "rather than implying a build will run.",
        }, indent=2)
    return json.dumps({
        "error": "no_connector",
        "detail": f"deploy_via is {via or 'unset'}. Bind a deploy connector before "
                  "asking for pipeline operations.",
    }, indent=2)


async def _pipelines_connector(ctx: Dict[str, Any]):
    """The connector, authenticating as the person in this conversation.

    ONE PLACE FOR A PAT, AND IT IS PER-USER-PER-PROJECT. Azure DevOps records whoever's
    credential made the call, so a shared tenant token makes every action show the same
    service identity and "who did this" stops having an answer. The session's user is
    passed as `owner_id` so their own saved credential is used.

    No fallback to a shared credential, deliberately: running as somebody else and
    recording it as them is worse than not running.
    """
    from config.connector_factory import get_connector_for_session

    return await get_connector_for_session(
        "azure_pipelines", ctx["tenant_id"], project_id=ctx["project_id"],
        agent_id="deployment", owner_id=ctx.get("user_id") or "",
    )


def _connector_problem(exc: Exception) -> str:
    """Name which problem it was. They have different fixes."""
    kind = type(exc).__name__
    if kind == "ConnectorAccessDenied":
        detail = ("This project has not granted the Deployment agent access to Azure "
                  "Pipelines. Grant it rather than reconnecting Azure DevOps.")
    elif kind == "ConnectorNotAvailableError":
        detail = (
            "You have no Azure DevOps credential saved on this project, so there is no "
            "identity to act as. Add one under Integrations. It deliberately does not "
            "fall back to a shared token — Azure DevOps records whoever's credential "
            "made the call, and running as somebody else would record it against them."
        )
    else:
        detail = f"Azure Pipelines call failed ({kind})."
    return json.dumps({"error": kind, "detail": detail}, indent=2)


# ── reads ────────────────────────────────────────────────────────────────────


@tool
async def list_pipelines() -> str:
    """List the Azure Pipelines defined in this project's ADO project.

    Call before offering to create one: a pipeline that already exists should be run,
    not duplicated under a second name.
    """
    ctx = _ctx()
    if (bad := _unsupported(ctx["deploy_via"])):
        return bad
    if not ctx["ado_project"]:
        return json.dumps({"error": "no_project",
                           "detail": "No ADO project is bound to this session."})
    try:
        conn = await _pipelines_connector(ctx)
        found = await conn.read_adapter("list_pipelines", project=ctx["ado_project"])
    except Exception as exc:  # noqa: BLE001
        return _connector_problem(exc)
    return json.dumps({"project": ctx["ado_project"], "count": len(found),
                       "pipelines": found}, indent=2, default=str)


@tool
async def get_pipeline_runs(pipeline_id: int, top: int = 10) -> str:
    """Recent runs of one pipeline, newest first.

    A run that has not finished reports `status: running` and a NULL result. That is
    not a failure — do not describe it as one.
    """
    ctx = _ctx()
    if (bad := _unsupported(ctx["deploy_via"])):
        return bad
    try:
        conn = await _pipelines_connector(ctx)
        runs = await conn.read_adapter(
            "list_runs", project=ctx["ado_project"], pipeline_id=int(pipeline_id),
            top=int(top),
        )
    except Exception as exc:  # noqa: BLE001
        return _connector_problem(exc)
    return json.dumps({"pipeline_id": pipeline_id, "runs": runs}, indent=2, default=str)


@tool
async def get_run_status(pipeline_id: int, run_id: int) -> str:
    """The state of one pipeline run, and WHAT FAILED when it failed.

    On a failure this also reads the run timeline and returns the failing stages with
    their error messages. "The pipeline failed" is not a report anybody can act on, and
    the platform already has the detail.
    """
    ctx = _ctx()
    if (bad := _unsupported(ctx["deploy_via"])):
        return bad
    try:
        conn = await _pipelines_connector(ctx)
        run = await conn.read_adapter(
            "get_run", project=ctx["ado_project"], pipeline_id=int(pipeline_id),
            run_id=int(run_id),
        )
        out: Dict[str, Any] = {"run": run}
        if run.get("result") in ("failed", "canceled", "partially_succeeded"):
            timeline = await conn.read_adapter(
                "get_run_timeline", project=ctx["ado_project"], run_id=int(run_id)
            )
            out["failed_stages"] = timeline.get("failed", [])
    except Exception as exc:  # noqa: BLE001
        return _connector_problem(exc)
    return json.dumps(out, indent=2, default=str)


@tool
async def list_service_connections() -> str:
    """What this ADO project can actually deploy to.

    Check before writing a pipeline that references one: YAML naming a service
    connection the project does not have fails on its first run.
    """
    ctx = _ctx()
    if (bad := _unsupported(ctx["deploy_via"])):
        return bad
    try:
        conn = await _pipelines_connector(ctx)
        found = await conn.read_adapter(
            "list_service_connections", project=ctx["ado_project"]
        )
    except Exception as exc:  # noqa: BLE001
        return _connector_problem(exc)
    if not found:
        return json.dumps({
            "service_connections": [],
            "detail": "This ADO project has no service connections. A deployment "
                      "pipeline needs one to reach a cluster or registry — it has to "
                      "be created in Azure DevOps, not here.",
        }, indent=2)
    return json.dumps({"service_connections": found}, indent=2, default=str)


# ── gated: these file a request, they do not act ─────────────────────────────


async def _file_request(action: str, request: Dict[str, Any], ctx: Dict[str, Any]) -> str:
    """Create the pending deployment row and describe what has NOT happened."""
    from shared.db import get_db_session_for_tenant
    from shared.services import deployment_gate

    if not ctx["tenant_id"] or not ctx["project_id"]:
        return json.dumps({
            "error": "no_project",
            "detail": "This conversation is not attached to a project, so there is "
                      "nobody to approve a deployment.",
        }, indent=2)

    requester = str(get_user_id() or "")
    if not requester:
        # Without an identity the self-approval rule cannot be enforced, so the gate
        # refuses. Better here, with a clear reason, than as a constraint violation.
        return json.dumps({
            "error": "no_requester",
            "detail": "The requesting user is unknown, so this cannot be attributed "
                      "or approved.",
        }, indent=2)

    try:
        async with get_db_session_for_tenant(ctx["tenant_id"]) as db:
            dep = await deployment_gate.request_deployment(
                db, tenant_id=ctx["tenant_id"], project_id=ctx["project_id"],
                action=action, target_kind="azure_pipelines",
                environment=ctx["environment"], request=request,
                requested_by=requester,
            )
            # Read everything needed BEFORE the block exits. The context manager
            # commits on the way out, and the transaction-local RLS tenant goes with
            # it — a read after that returns nothing.
            filed = {"id": str(dep.id), "environment": dep.environment,
                     "action": dep.action}
    except deployment_gate.DeploymentGateError as exc:
        return json.dumps({"error": exc.code, "detail": exc.reason}, indent=2)
    except Exception as exc:  # noqa: BLE001
        logger.error("filing a deployment request failed: %s", type(exc).__name__)
        return json.dumps({"error": type(exc).__name__,
                           "detail": "Could not file the deployment request."}, indent=2)

    return json.dumps({
        "status": "awaiting_approval",
        "deployment_id": filed["id"],
        "action": filed["action"],
        "environment": filed["environment"],
        "request": request,
        "nothing_has_run": True,
        "detail": (
            "NOTHING HAS HAPPENED YET. This is a request waiting for someone holding "
            "artifact:approve_deployment to approve it on the project's Deployment "
            "screen. It cannot be approved by the person who asked for it. Tell the "
            "user exactly this — do not describe the deployment as started, queued or "
            "in progress."
        ),
    }, indent=2)


@tool
async def request_pipeline_creation(name: str, yaml_path: str = "azure-pipelines.yml") -> str:
    """Ask for a pipeline to be CREATED in Azure DevOps. Requires approval.

    THIS DOES NOT CREATE ANYTHING. It files a request a human has to approve, and
    returns `awaiting_approval` with the id. Never tell the user the pipeline exists.

    The YAML must already be committed on the repo's default branch — ADO resolves the
    path when the pipeline is created and rejects one that does not exist. That means
    the deployment PR merges FIRST, then the pipeline is created against the merged
    file.
    """
    ctx = _ctx()
    if (bad := _unsupported(ctx["deploy_via"])):
        return bad
    if not name:
        return json.dumps({"error": "no_name",
                           "detail": "A pipeline needs a name."}, indent=2)
    return await _file_request("create_pipeline", {
        "name": name, "yaml_path": yaml_path, "ado_project": ctx["ado_project"],
        "repository": ctx["repo_name"],
    }, ctx)


@tool
async def request_pipeline_run(pipeline_id: int, branch: str = "") -> str:
    """Ask for a pipeline to be RUN. Requires approval. This is what deploys.

    THIS DOES NOT START ANYTHING. It files a request a human has to approve, and
    returns `awaiting_approval` with the id. Do not say the deployment has started, is
    queued, or is in progress — none of those are true yet.

    One approval covers one run. A second deployment needs a second request.
    """
    ctx = _ctx()
    if (bad := _unsupported(ctx["deploy_via"])):
        return bad
    return await _file_request("run_pipeline", {
        "pipeline_id": int(pipeline_id),
        "branch": branch or ctx["branch"] or "",
        "ado_project": ctx["ado_project"],
    }, ctx)


@tool
async def check_deployment_request(deployment_id: str) -> str:
    """Where a filed deployment request has got to: approved, rejected, or still
    waiting — and what it produced once it ran."""
    from sqlalchemy import select

    from shared.db import get_db_session_for_tenant
    from shared.models.orm import Deployment

    ctx = _ctx()
    if not ctx["tenant_id"]:
        return json.dumps({"error": "no_tenant"}, indent=2)
    try:
        import uuid as _uuid

        _uuid.UUID(str(deployment_id))
    except (ValueError, AttributeError, TypeError):
        return json.dumps({"error": "not_found",
                           "detail": "No such deployment request."}, indent=2)
    try:
        async with get_db_session_for_tenant(ctx["tenant_id"]) as db:
            dep = (await db.execute(
                select(Deployment).where(
                    Deployment.id == deployment_id,
                    Deployment.tenant_id == ctx["tenant_id"],
                )
            )).scalar_one_or_none()
            if dep is None:
                return json.dumps({"error": "not_found",
                                   "detail": "No such deployment request."}, indent=2)
            out = {
                "deployment_id": str(dep.id),
                "action": dep.action,
                "environment": dep.environment,
                "approval_status": dep.approval_status,
                "approved_by": dep.approved_by,
                "rejection_reason": dep.rejection_reason,
                "execution_status": dep.execution_status,
                "external_url": dep.external_url,
                "outcome": dep.outcome,
            }
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"error": type(exc).__name__}, indent=2)

    if out["approval_status"] == "pending":
        out["detail"] = ("Still waiting for approval. Nothing has run.")
    elif out["approval_status"] == "rejected":
        out["detail"] = "This request was rejected. Nothing ran."
    elif out["execution_status"] == "not_started":
        out["detail"] = ("Approved, but not started yet.")
    return json.dumps(out, indent=2, default=str)
