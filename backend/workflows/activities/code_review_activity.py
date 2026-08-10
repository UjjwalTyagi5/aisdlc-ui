"""Temporal activity wrapper for the code review agent.

Follows the same pattern as requirements/design/development/testing activities:
  - Idempotency check via check_existing_artifact
  - Lazy import of the LangGraph graph
  - Connector injection for tenant context
  - Audit callback handler for token/cost tracking
  - Clarification detection post-hoc
  - write_and_notify for artifact persistence
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from langchain_core.messages import HumanMessage
from temporalio import activity

from shared.models.artifacts import CodeReviewArtifact
from shared.models.workflow_models import ClarificationRequest, SDLCWorkflowInput
from shared.services.metrics import TEMPORAL_ACTIVITY_DURATION
from workflows.activities._base import (
    check_existing_artifact,
    detect_clarification_need,
    mcp_tools_for_stage,
    stage_connector_kind,
    write_and_notify,
)
from workflows.activities.pipeline_session import _read_run_upstream, pipeline_session

logger = logging.getLogger(__name__)

_ACTIVITY_NAME = "run_code_review_activity"


async def _read_run_upstream_for_review(input: SDLCWorkflowInput) -> Optional[dict]:
    """Return this run's `development_artifacts` dict, or None.

    This is the repo pointer the pipeline review must clone+diff against — the
    output of THIS run's development stage, not a project-latest sibling.
    """
    try:
        upstream = await _read_run_upstream(str(input.run_id))
    except Exception as exc:  # never break the activity on a read
        logger.warning("_read_run_upstream_for_review(%s) failed: %s", input.run_id, exc)
        return None
    dev = upstream.get("development_artifacts")
    return dev if isinstance(dev, dict) else None


def _repo_ref_from_dev(dev: Optional[dict]) -> Optional[dict]:
    """Build a Bridge repo_ref from development_artifacts, or None if no repo url."""
    if not dev or not dev.get("repo_url"):
        return None
    return {
        "repo_url": dev.get("repo_url"),
        "ref": dev.get("branch_name"),
        "base": dev.get("target_branch") or "main",
    }


def _capture_review_artifact(
    input: SDLCWorkflowInput, final_state: dict, last_artifact: Optional[dict]
) -> CodeReviewArtifact:
    """Prefer the structured `submit_code_review` output; fall back to the summary.

    The rich standalone artifact (`shared.models.code_review.CodeReviewArtifact`,
    stored in the review session's `last_artifact`) carries findings/coverage/
    conformance/recommendation. Map it onto the pipeline persistence model. When the
    agent never called `submit_code_review`, fall back to the final-message summary —
    identical to the pre-Task-7 behaviour.
    """
    if last_artifact:
        return CodeReviewArtifact(
            review_summary=last_artifact.get("summary"),
            merge_recommendation=last_artifact.get("merge_recommendation"),
            findings=last_artifact.get("findings") or [],
            version=input.agent_version,
        )

    messages = final_state.get("messages", [])
    review_content: Optional[str] = None
    if messages:
        last = messages[-1]
        review_content = getattr(last, "content", None)
        if isinstance(review_content, list):
            review_content = " ".join(
                block.get("text", "") if isinstance(block, dict) else str(block)
                for block in review_content
            )
    return CodeReviewArtifact(review_summary=review_content, version=input.agent_version)


@activity.defn(name=_ACTIVITY_NAME)
async def run_code_review_activity(input: SDLCWorkflowInput) -> ClarificationRequest | CodeReviewArtifact:
    """Idempotent code review agent Temporal activity."""
    start = time.monotonic()
    status = "ok"
    try:
        existing = await check_existing_artifact(
            input.run_id, "code_review", input.agent_version, tenant_id=input.tenant_id
        )
        if existing is not None:
            return CodeReviewArtifact(**existing)

        from agents_orchestrator.code_review_agent.agents.reviewer import (  # noqa: PLC0415
            app as _code_review_graph,
        )

        from config.connectors.context import set_connector, clear_connector  # noqa: PLC0415
        from config.connector_factory import get_connector_for_session  # noqa: PLC0415

        _connector_injected = False
        if input.tenant_id:
            try:
                set_connector(
                    await get_connector_for_session(
                        kind=stage_connector_kind(input, "code_review"),
                        tenant_id=input.tenant_id,
                    )
                )
                _connector_injected = True
            except Exception:
                _connector_injected = False

        from contextlib import ExitStack  # noqa: PLC0415

        from shared.observability import build_agent_callbacks  # noqa: PLC0415

        _callbacks, _trace_cm = build_agent_callbacks(
            run_id=str(input.run_id),
            tenant_id=input.tenant_id or "",
            agent_type="code_review",
            model=input.model_id,
            offering_id=input.offering_id,
            project_id=input.project_id,
        )
        _trace_stack = ExitStack()
        _trace_stack.enter_context(_trace_cm)
        _cfg = {
            "configurable": {"thread_id": str(input.run_id)},
            "recursion_limit": 100,
            "callbacks": _callbacks,
        }

        # Resolve THIS run's dev repo pointer; clone + diff via the Bridge when present.
        dev = await _read_run_upstream_for_review(input)
        repo_ref = _repo_ref_from_dev(dev)

        async with pipeline_session(
            input, "code_review", needs_repo=bool(repo_ref), repo_ref=repo_ref
        ) as ps:
            # Seed the review session with the run-scoped workspace + diff so the CR
            # tools (read_repo_file / search_repo / submit_code_review metrics) resolve
            # the correct clone. RunWorkspace.changed_files is list[str]; the CR tools
            # consume list[dict] keyed on "path" (+ optional added/removed), so convert.
            if getattr(ps, "_workspace", None):
                from agents_orchestrator.code_review_agent.config.session_state import (  # noqa: PLC0415
                    get_session,
                )

                s = get_session(str(input.run_id))
                s.work_dir = ps._workspace.work_dir
                s.diff_text = ps._workspace.diff_text or ""
                s.changed_files = [{"path": p} for p in (ps._workspace.changed_files or [])]
                s.tenant_id = input.tenant_id or ""
                s.project_id = str(input.project_id)

            try:
                async with mcp_tools_for_stage(input, "code_review"):
                    if input.clarification_answer:
                        final_state = await _code_review_graph.ainvoke(
                            {"messages": [HumanMessage(content=input.clarification_answer)]},
                            config=_cfg,
                        )
                    else:
                        prompt = (
                            f"Review code changes for project_id={input.project_id}. "
                            f"trigger={input.trigger}"
                            + (f", work_item_id={input.work_item_id}" if input.work_item_id else "")
                            + ". Analyze the code diff, run Semgrep if applicable, and produce your review findings."
                        )
                        final_state = await _code_review_graph.ainvoke(
                            {"messages": [HumanMessage(content=prompt)], "tenant_id": input.tenant_id, "model_id": input.model_id, "offering_id": input.offering_id},
                            config=_cfg,
                        )
            finally:
                _trace_stack.close()
                if _connector_injected:
                    clear_connector()

            clarification = detect_clarification_need(final_state, str(input.run_id), "code_review")
            if clarification is not None:
                return clarification

            # Prefer the structured submit_code_review output; else summary fallback.
            from agents_orchestrator.code_review_agent.config.session_state import (  # noqa: PLC0415
                get_session,
            )

            _last = get_session(str(input.run_id)).last_artifact
            artifact = _capture_review_artifact(input, final_state, _last)

            await write_and_notify(input.run_id, "code_review", artifact.model_dump(), tenant_id=input.tenant_id)
            return artifact

    except Exception:
        status = "error"
        raise
    finally:
        elapsed = time.monotonic() - start
        TEMPORAL_ACTIVITY_DURATION.labels(
            activity=_ACTIVITY_NAME, status=status
        ).observe(elapsed)
