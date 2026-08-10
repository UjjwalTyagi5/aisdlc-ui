"""Temporal activity wrapper for the development agent (M5-04, D-01).

Idempotency contract (D-05): if an artifact already exists for
(run_id, "development", agent_version), it is returned immediately —
the LangGraph graph is NOT re-invoked.

No self-timeout is declared here. start_to_close_timeout is the exclusive
responsibility of the workflow caller (plan 06, D-09) — T-M5-09 transfer.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from langchain_core.messages import HumanMessage, SystemMessage
from temporalio import activity
from temporalio.exceptions import ApplicationError

from shared.capabilities.preflight import gap_message
from shared.models.artifacts import DevelopmentArtifact
from shared.models.workflow_models import ClarificationRequest, ChangeRequest, SDLCWorkflowInput
from shared.services.metrics import TEMPORAL_ACTIVITY_DURATION
from workflows.activities._base import (
    check_existing_artifact,
    detect_clarification_need,
    resolved_tools_for_stage,
    stage_connector_kind,
    write_and_notify,
)
from workflows.activities.pipeline_session import pipeline_session

logger = logging.getLogger(__name__)

_ACTIVITY_NAME = "run_development_activity"


async def _resolve_dev_repo_ref(input: SDLCWorkflowInput) -> Optional[dict]:
    """Return a repo_ref dict if the project has a ready pulled workspace, else None.

    Reads the per-project dev workspace written by the standalone pull-repo flow.
    If the workspace is absent or not yet ready, returns None so the activity
    falls back to the dev agent's own clone-on-demand path — current behaviour
    is fully preserved.
    """
    if not input.tenant_id or not input.project_id:
        return None
    try:
        from shared.services.dev_workspace_store import get_for_project  # noqa: PLC0415

        ws = await get_for_project(str(input.tenant_id), str(input.project_id))
    except Exception as exc:
        logger.warning(
            "_resolve_dev_repo_ref(%s/%s) lookup failed: %s",
            input.tenant_id, input.project_id, exc,
        )
        return None

    if ws is None or ws.get("status") != "ready":
        return None

    return {
        "repo_url": ws["remote_url"],
        "ref": ws.get("branch") or "main",
    }


def _build_dev_prompt(
    has_design: bool,
    change_request: Optional[ChangeRequest],
    work_item_id: Optional[str],
    project_id: str,
) -> str:
    """Deterministic mode resolution (spec §3.2) — pure, testable, no I/O.

    Priority: design-driven (Mode 1) > change-request-driven (Mode 2) >
    work-item-only > no-intent (run-level clarification). Never raises.
    """
    if has_design:
        return (
            f"Implement to the approved design for project {project_id}. "
            f"Read the design and requirements via the read_design_artifact tool, "
            f"then implement on an isolated branch and open a PR."
            + (f" Linked work item: {work_item_id}." if work_item_id else "")
        )
    if change_request is not None:
        return (
            f"Direct change request for the EXISTING repository (project {project_id}). "
            f"Change: {change_request.text}"
            + (f" | type: {change_request.kind}" if change_request.kind else "")
            + (f" | focus paths: {', '.join(change_request.target_paths)}" if change_request.target_paths else "")
            + ". Work WITHIN the existing codebase — clone it, search/understand it, "
            f"make ONLY the requested change, build/lint, and open a PR. "
            f"Do NOT scaffold a new project."
            + (f" Linked work item: {change_request.work_item_id}." if change_request.work_item_id else "")
        )
    if work_item_id:
        return (
            f"Implement the change described in work item {work_item_id} for project {project_id}. "
            f"Read the work item, then work within the existing repo, build/lint, and open a PR."
        )
    return (
        f"No design or change request was provided for project {project_id}. "
        f"Ask the user what change to implement before doing any work."
    )


@activity.defn(name=_ACTIVITY_NAME)
async def run_development_activity(input: SDLCWorkflowInput) -> ClarificationRequest | DevelopmentArtifact:
    """Idempotent development agent Temporal activity.

    On the hot path (artifact already persisted for this version): returns
    the existing DevelopmentArtifact without invoking the LangGraph graph.
    On the cold path: invokes the development LangGraph graph, maps the
    final state to a DevelopmentArtifact, persists via write_and_notify,
    and returns the artifact.

    M10.3 (REQ-M10-08 resume half): when input.clarification_answer is set,
    resumes the SAME thread_id=str(run_id) PostgresSaver checkpoint by
    injecting only the answer as the next HumanMessage (D-M10-02 — no
    agent-logic change). After either invocation path, detect_clarification_need
    inspects final_state; if the agent ended its turn with another question,
    a ClarificationRequest is returned WITHOUT writing an artifact (Pitfall 6).
    """
    start = time.monotonic()
    status = "ok"
    try:
        # D-05: idempotency check
        existing = await check_existing_artifact(
            input.run_id, "development", input.agent_version, tenant_id=input.tenant_id
        )
        if existing is not None:
            return DevelopmentArtifact(**existing)

        # Cold path — lazy import to avoid transitive dependency failures at startup.
        from agents_orchestrator.development_agent.agents.dev_agent import (  # noqa: PLC0415
            app as _development_graph,
            tools as _dev_tools,
        )
        from agents_orchestrator.development_agent.prompts.dev_agent_prompt import (  # noqa: PLC0415
            DEV_SYS_MESSAGE as _dev_base_prompt,
        )

        # Resolve the per-project pulled workspace so the dev session's file/git
        # tools can use it directly (binding happens below after pipeline_session
        # has set up the session_id contextvar and optionally cloned the repo).
        repo_ref = await _resolve_dev_repo_ref(input)

        async with pipeline_session(
            input, "development",
            needs_repo=bool(repo_ref),
            repo_ref=repo_ref,
        ) as ps:
            # Bind run-scoped work_dir onto the dev session state so _get_work_dir()
            # in file/git/generation tools resolves the correct workspace instead of
            # falling back to the None-keyed empty path.  DevSessionState only has
            # work_dir (tenant_id / project_id fields do not exist on the dataclass —
            # those values travel via the activity input and are not persisted here).
            if ps.work_dir:
                from agents_orchestrator.development_agent.config.session_state import (  # noqa: PLC0415
                    get_session as _get_dev_session,
                )
                _dev_s = _get_dev_session(str(input.run_id))
                _dev_s.work_dir = ps.work_dir

            # Inject the tenant's board connector for the run so development work-item
            # tools (config.connectors.context.get_connector) resolve the tenant's
            # board. Mirrors requirements_activity / the Redis worker. Fail-soft +
            # clear_connector() in finally (REQ-M3-10).
            from config.connectors.context import set_connector, clear_connector  # noqa: PLC0415
            from config.connector_factory import get_connector_for_session  # noqa: PLC0415

            _connector_injected = False
            if input.tenant_id:
                try:
                    set_connector(
                        await get_connector_for_session(
                            kind=stage_connector_kind(input, "development"),
                            tenant_id=input.tenant_id,
                        )
                    )
                    _connector_injected = True
                except Exception:
                    _connector_injected = False

            # Per-run audit + token/cost + Langfuse tracing + usage meter (one pipeline
            # trace per run, seeded from run_id; meter powers RPM/TPM/cost enforcement).
            from contextlib import ExitStack  # noqa: PLC0415

            from shared.observability import build_agent_callbacks  # noqa: PLC0415

            _callbacks, _trace_cm = build_agent_callbacks(
                run_id=str(input.run_id),
                tenant_id=input.tenant_id or "",
                agent_type="development",
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

            try:
                async with resolved_tools_for_stage(
                    input, "development", base_tools=_dev_tools, base_prompt=_dev_base_prompt
                ) as run:
                    # Run-start hard block (DP6): surface capability gap as a terminal,
                    # actionable error so the workflow does not retry a broken configuration.
                    if run.gap:
                        raise ApplicationError(
                            gap_message("development", run.gap),
                            non_retryable=True,
                        )

                    # Capability-resolved profile prompt: prepend only when the profile
                    # resolution produced additional instructions; avoids a no-op SystemMessage
                    # on the dev/no-profile path where run.prompt equals the base prompt.
                    _profile_prefix: list = (
                        [SystemMessage(content=run.prompt)] if run.prompt and run.prompt != _dev_base_prompt else []
                    )

                    if input.clarification_answer:
                        # Resume path (M10.3): inject ONLY the answer as the next
                        # HumanMessage under the SAME thread_id so LangGraph resumes
                        # from the PostgresSaver checkpoint at the pre-question state.
                        # Do not re-send tenant_id or the initial prompt.
                        # Profile prefix is NOT injected here — it already exists in the checkpoint.
                        final_state = await _development_graph.ainvoke(
                            {"messages": [HumanMessage(content=input.clarification_answer)]},
                            config=_cfg,
                        )
                    else:
                        # Mode resolution (spec §3.2): design-driven > change-request > work-item > none.
                        has_design = (
                            await check_existing_artifact(
                                input.run_id, "design", input.agent_version, tenant_id=input.tenant_id
                            )
                        ) is not None
                        prompt = _build_dev_prompt(
                            has_design=has_design,
                            change_request=input.change_request,
                            work_item_id=input.work_item_id,
                            project_id=input.project_id,
                        )
                        final_state = await _development_graph.ainvoke(
                            {
                                "messages": [*_profile_prefix, HumanMessage(content=prompt)],
                                "tenant_id": input.tenant_id,
                                "model_id": input.model_id,
                                "offering_id": input.offering_id,
                            },
                            config=_cfg,
                        )
            finally:
                _trace_stack.close()
                if _connector_injected:
                    clear_connector()

            # M10.3: post-hoc inspection — if the agent ended its turn with
            # another question, return ClarificationRequest WITHOUT writing an
            # artifact (Pitfall 6).
            clarification = detect_clarification_need(final_state, str(input.run_id), "development")
            if clarification is not None:
                return clarification

            # Extract summary from the final agent message
            messages = final_state.get("messages", [])
            code_summary: Optional[str] = None
            if messages:
                last = messages[-1]
                code_summary = getattr(last, "content", None)
                if isinstance(code_summary, list):
                    code_summary = " ".join(
                        block.get("text", "") if isinstance(block, dict) else str(block)
                        for block in code_summary
                    )

            artifact = DevelopmentArtifact(
                code_summary=code_summary,
                version=input.agent_version,
            )

            await write_and_notify(input.run_id, "development", artifact.model_dump(), tenant_id=input.tenant_id)
            return artifact

    except Exception:
        status = "error"
        raise
    finally:
        elapsed = time.monotonic() - start
        TEMPORAL_ACTIVITY_DURATION.labels(
            activity=_ACTIVITY_NAME, status=status
        ).observe(elapsed)
