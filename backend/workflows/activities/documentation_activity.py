"""Temporal activity wrapper for the standalone Documentation agent.

Follows the same pattern as deployment_activity.py:
  - Idempotency check via check_existing_artifact (returns raw dict — NOT a typed
    reconstruction, which would crash on shape mismatch; workflow only needs the dict)
  - Pipeline Session Bridge for repo checkout (dev artifact repo_ref)
  - Session state seeding (work_dir / tenant_id / project_id + PR fields)
  - DocumentationArtifact capture from DocSessionState.generated_docs
  - write_and_notify for persistence
  - Prometheus timing
"""
from __future__ import annotations

import logging
import time

from langchain_core.messages import HumanMessage
from temporalio import activity

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

_ACTIVITY_NAME = "run_documentation_activity"


def _get_doc_session(run_id):
    """Return the documentation agent's per-session state, keyed by run_id."""
    from agents_orchestrator.documentation_agent.config.session_state import get_session  # noqa: PLC0415

    return get_session(str(run_id))


def _capture_doc_artifact(run_id, final_state) -> dict:
    """Build a DocumentationArtifact from DocSessionState.generated_docs.

    DocSessionState.generated_docs accumulates dicts via save_document throughout
    the run; each dict matches GeneratedDoc fields exactly ({id, type, title,
    filename, format, path, contents, bytes}).  DocContext.mode must be "branch"
    (Literal["branch","pr"]) — "pipeline" would fail Pydantic validation.
    """
    from shared.models.documentation import DocumentationArtifact, DocContext  # noqa: PLC0415

    s = _get_doc_session(run_id)
    docs_raw = getattr(s, "generated_docs", []) or []

    # Build DocContext from session fields with safe defaults.
    context = DocContext(
        repo_name=getattr(s, "repo_name", "") or "",
        ado_project=getattr(s, "ado_project", "") or "",
        mode="branch",
        source_branch=getattr(s, "source_branch", "") or "",
        pr_id=getattr(s, "pr_id", None) or None,
        head_sha=getattr(s, "head_sha", "") or "",
        languages=getattr(s, "languages", []) or [],
        upstream_summary=getattr(s, "upstream_summary", "") or "",
    )

    return DocumentationArtifact(
        context=context,
        documents=docs_raw,
        pr_url=getattr(s, "pr_url", None),
        status="ready" if docs_raw else "idle",
    ).model_dump()


@activity.defn(name=_ACTIVITY_NAME)
async def run_documentation_activity(input: SDLCWorkflowInput) -> ClarificationRequest | dict:
    """Idempotent documentation agent Temporal activity.

    Idempotency: returns the raw persisted dict when an artifact already exists for
    this (run_id, "documentation", agent_version) — no typed reconstruction to avoid
    shape-mismatch crashes (Task-8 / Task-11 pattern).

    Fresh path: clones the repo via Pipeline Session Bridge using the dev artifact's
    repo_url + branch_name, seeds DocSessionState so open_docs_pr and inspect_repo
    resolve the correct checkout, invokes the LangGraph compiler graph, captures the
    artifact from session.generated_docs, persists it, and returns the raw dict.
    """
    start = time.monotonic()
    status = "ok"
    try:
        # Idempotency — return the raw persisted dict, not a typed reconstruction.
        existing = await check_existing_artifact(
            input.run_id, "documentation", input.agent_version, tenant_id=input.tenant_id
        )
        if existing is not None:
            return existing

        # Lazy import keeps module-level startup clean (heavy doc agent deps).
        from agents_orchestrator.documentation_agent.agents.compiler import (  # noqa: PLC0415
            app as _doc_graph,
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
            f"Compile the full doc set + run summary + RTM from all upstream artifacts "
            f"for project_id={input.project_id}. "
            f"trigger={input.trigger}"
            + (f", work_item_id={input.work_item_id}" if input.work_item_id else "")
            + ". Call inspect_repo first, then read_upstream_artifacts, then call "
            "save_document for each deliverable (overview, SDD, API reference, RTM, "
            "run summary, changelog). Generate a complete documentation set."
        )

        async with pipeline_session(
            input, "documentation", needs_repo=bool(repo_ref), repo_ref=repo_ref
        ) as ps:
            # Seed the doc session so all tools resolve the correct checkout and
            # open_docs_pr can push + open a PR in pipeline mode.
            if getattr(ps, "_workspace", None):
                s = _get_doc_session(input.run_id)
                s.work_dir = ps._workspace.work_dir
                s.tenant_id = input.tenant_id or ""
                s.project_id = str(input.project_id)

                # Seed branch so open_docs_pr knows the PR target.
                s.source_branch = dev.get("branch_name") or "main"

                # Resolve PAT via connector (same path as ado_repos / run_workspace).
                try:
                    from shared.services import ado_repos as _ado_repos  # noqa: PLC0415
                    _org_url, _pat = await _ado_repos.resolve_auth(input.tenant_id or "")
                    s.pat = _pat or ""
                except Exception as _pat_exc:
                    logger.warning(
                        "documentation_activity: PAT resolve failed (%s); s.pat left empty",
                        _pat_exc,
                    )

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
                        logger.warning(
                            "documentation_activity: repo URL parse failed (%s); falling back to dev keys",
                            _parse_exc,
                        )
                # Fallback: accept explicit keys from the dev artifact if parsing failed.
                if not s.repo_name:
                    s.repo_name = dev.get("repo_name") or ""
                if not s.ado_project:
                    s.ado_project = dev.get("ado_project") or ""

            _cfg = {
                "configurable": {"thread_id": str(input.run_id)},
                "recursion_limit": 100,
            }

            async with mcp_tools_for_stage(input, "documentation"):
                if input.clarification_answer:
                    final_state = await _doc_graph.ainvoke(
                        {"messages": [HumanMessage(content=input.clarification_answer)]},
                        config=_cfg,
                    )
                else:
                    final_state = await _doc_graph.ainvoke(
                        {
                            "messages": [HumanMessage(content=prompt)],
                            "tenant_id": input.tenant_id,
                            "model_id": input.model_id,
                            "offering_id": input.offering_id,
                        },
                        config=_cfg,
                    )

            # detect_clarification_need runs inside the session context so the
            # session_id contextvar remains bound (aligns with deployment_activity.py).
            clarification = detect_clarification_need(final_state, str(input.run_id), "documentation")
            if clarification is not None:
                return clarification

            art = _capture_doc_artifact(input.run_id, final_state)
            art.setdefault("version", input.agent_version)

            await write_and_notify(input.run_id, "documentation", art, tenant_id=input.tenant_id)
            return art

    except Exception:
        status = "error"
        raise
    finally:
        elapsed = time.monotonic() - start
        TEMPORAL_ACTIVITY_DURATION.labels(activity=_ACTIVITY_NAME, status=status).observe(elapsed)
