"""Temporal activity wrapper for the standalone Deployment agent.

Follows the same pattern as security_activity.py:
  - Idempotency check via check_existing_artifact (returns raw dict — NOT a typed
    reconstruction, which would crash on shape mismatch; workflow only needs the dict)
  - Pipeline Session Bridge for repo checkout (dev artifact repo_ref)
  - Session state seeding (work_dir / tenant_id / project_id)
  - DeploymentArtifact fallback when session.last_artifact is absent
  - write_and_notify for persistence
  - Prometheus timing
"""
from __future__ import annotations

import logging
import time

from langchain_core.messages import HumanMessage
from temporalio import activity

from shared.models.deployment import DeploymentArtifact
from shared.models.workflow_models import ClarificationRequest, SDLCWorkflowInput
from shared.services.metrics import TEMPORAL_ACTIVITY_DURATION
from workflows.activities._base import (
    check_existing_artifact,
    detect_clarification_need,
    mcp_tools_for_stage,
    write_and_notify,
)
from workflows.activities.pipeline_session import _read_run_upstream, pipeline_session

logger = logging.getLogger(__name__)

_ACTIVITY_NAME = "run_deployment_activity"


def _get_deploy_session(run_id):
    """Return the deployment agent's per-session state, keyed by run_id."""
    from agents_orchestrator.deployment_agent.config.session_state import get_session  # noqa: PLC0415

    return get_session(str(run_id))


def _capture_deploy_artifact(run_id, final_state) -> dict:
    """Prefer the structured submit_release output; fall back to message summary.

    submit_release stores the rich artifact dict in session.last_artifact.
    When absent (agent did not call submit_release), build a minimal
    DeploymentArtifact with sensible defaults so the pipeline never stalls.
    All DeploymentArtifact fields have defaults — no required-field crash risk.
    """
    s = _get_deploy_session(run_id)
    last_artifact = getattr(s, "last_artifact", None)
    if last_artifact:
        return last_artifact

    messages = (final_state or {}).get("messages", [])
    summary = None
    if messages:
        last = messages[-1]
        summary = getattr(last, "content", None)
        if isinstance(summary, list):
            summary = " ".join(
                block.get("text", "") if isinstance(block, dict) else str(block)
                for block in summary
            )
    return DeploymentArtifact(
        summary=summary if isinstance(summary, str) else "",
        readiness="conditional",
        risk_score="none",
        risk_rationale="",
        release_decision="conditional",
        release_justification="",
        status="assessed",
    ).model_dump()


@activity.defn(name=_ACTIVITY_NAME)
async def run_deployment_activity(input: SDLCWorkflowInput) -> ClarificationRequest | dict:
    """Idempotent deployment agent Temporal activity.

    Idempotency (D-05): returns the raw persisted dict when an artifact already
    exists for this (run_id, "deployment", agent_version) — no typed reconstruction
    to avoid shape-mismatch crashes on the idempotency path (Task-8 / Task-11 pattern).

    Fresh path: clones the repo via the Pipeline Session Bridge using the dev
    artifact's repo_url + branch_name, seeds the deploy session state, invokes
    the LangGraph deployer graph, captures the artifact, persists it, and returns
    the raw dict.
    """
    start = time.monotonic()
    status = "ok"
    try:
        # D-05 idempotency — return the raw persisted dict, not a typed reconstruction.
        existing = await check_existing_artifact(
            input.run_id, "deployment", input.agent_version, tenant_id=input.tenant_id
        )
        if existing is not None:
            return existing

        # Lazy import keeps module-level startup clean (heavy deploy agent deps).
        from agents_orchestrator.deployment_agent.agents.deployer import (  # noqa: PLC0415
            app as _deploy_graph,
        )

        # Resolve repo pointer from the development artifacts written by the dev activity.
        try:
            upstream = await _read_run_upstream(str(input.run_id))
        except Exception as exc:
            logger.warning("_read_run_upstream(%s) failed: %s", input.run_id, exc)
            upstream = {}
        dev = upstream.get("development_artifacts") or {}
        repo_ref = (
            {"repo_url": dev.get("repo_url"), "ref": dev.get("branch_name") or "main"}
            if dev.get("repo_url")
            else None
        )

        prompt = (
            f"Assess release readiness for project_id={input.project_id}. "
            f"trigger={input.trigger}"
            + (f", work_item_id={input.work_item_id}" if input.work_item_id else "")
            + ". Read upstream testing/security/dev artifacts, score change risk, "
            "generate deploy + rollback runbooks and a release decision, then call submit_release."
        )

        async with pipeline_session(
            input, "deployment", needs_repo=bool(repo_ref), repo_ref=repo_ref
        ) as ps:
            # Seed the deploy session so the deployer tools resolve the correct checkout.
            if getattr(ps, "_workspace", None):
                s = _get_deploy_session(input.run_id)
                s.work_dir = ps._workspace.work_dir
                s.tenant_id = input.tenant_id or ""
                s.project_id = str(input.project_id)

                # Seed ADO fields so open_deploy_pr can push + open a PR in pipeline mode.
                s.source_branch = dev.get("branch_name") or "main"

                # Resolve PAT via connector (same path as ado_repos / run_workspace).
                try:
                    from shared.services import ado_repos as _ado_repos  # noqa: PLC0415
                    _org_url, _pat = await _ado_repos.resolve_auth(input.tenant_id or "")
                    s.pat = _pat or ""
                except Exception as _pat_exc:
                    logger.warning("deployment_activity: PAT resolve failed (%s); s.pat left empty", _pat_exc)

                # Parse repo_name / ado_project from the dev artifact repo_url.
                _repo_url = dev.get("repo_url") or ""
                if _repo_url:
                    try:
                        from agents_orchestrator.testing_agent.Nodes.workspace import (  # noqa: PLC0415
                            _parse_ado_repo_url,
                        )
                        _parsed = _parse_ado_repo_url(_repo_url)
                        if _parsed:
                            s.ado_project, s.repo_name = _parsed
                    except Exception as _parse_exc:
                        logger.warning("deployment_activity: repo URL parse failed (%s); falling back to dev keys", _parse_exc)
                # Fallback: accept explicit keys from the dev artifact if parsing failed.
                if not s.repo_name:
                    s.repo_name = dev.get("repo_name") or ""
                if not s.ado_project:
                    s.ado_project = dev.get("ado_project") or ""

            _cfg = {
                "configurable": {"thread_id": str(input.run_id)},
                "recursion_limit": 100,
            }

            async with mcp_tools_for_stage(input, "deployment"):
                if input.clarification_answer:
                    final_state = await _deploy_graph.ainvoke(
                        {"messages": [HumanMessage(content=input.clarification_answer)]},
                        config=_cfg,
                    )
                else:
                    final_state = await _deploy_graph.ainvoke(
                        {
                            "messages": [HumanMessage(content=prompt)],
                            "tenant_id": input.tenant_id,
                            "model_id": input.model_id,
                            "offering_id": input.offering_id,
                        },
                        config=_cfg,
                    )

            # Finding 2: detect_clarification_need runs inside the session context
            # so the session_id contextvar remains bound (aligns with security_activity.py).
            clarification = detect_clarification_need(final_state, str(input.run_id), "deployment")
            if clarification is not None:
                return clarification

            art = _capture_deploy_artifact(input.run_id, final_state)
            art.setdefault("version", input.agent_version)

            await write_and_notify(input.run_id, "deployment", art, tenant_id=input.tenant_id)
            return art

    except Exception:
        status = "error"
        raise
    finally:
        elapsed = time.monotonic() - start
        TEMPORAL_ACTIVITY_DURATION.labels(activity=_ACTIVITY_NAME, status=status).observe(elapsed)
