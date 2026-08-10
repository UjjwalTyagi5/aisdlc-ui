"""Temporal activity wrapper for the requirements agent (M5-04, D-01).

Idempotency contract (D-05): if an artifact already exists for
(run_id, "requirements", agent_version), it is returned immediately —
the LangGraph graph is NOT re-invoked.

Graph import (post D-02): agents_orchestrator.requirements_agent.agents.planning
(NOT the legacy agents.requirements_agent path — T-M5-08 mitigation).

No self-timeout is declared here. start_to_close_timeout is the exclusive
responsibility of the workflow caller (plan 06, D-09) — T-M5-09 transfer.
"""
from __future__ import annotations

import time
from typing import Optional

from langchain_core.messages import HumanMessage, SystemMessage
from temporalio import activity
from temporalio.exceptions import ApplicationError

from shared.capabilities.preflight import gap_message
from shared.models.artifacts import RequirementsArtifact
from shared.models.workflow_models import ClarificationRequest, SDLCWorkflowInput
from shared.services.metrics import TEMPORAL_ACTIVITY_DURATION
from workflows.activities._base import (
    check_existing_artifact,
    detect_clarification_need,
    resolved_tools_for_stage,
    stage_connector_kind,
    write_and_notify,
)

_ACTIVITY_NAME = "run_requirements_activity"


@activity.defn(name=_ACTIVITY_NAME)
async def run_requirements_activity(input: SDLCWorkflowInput) -> ClarificationRequest | RequirementsArtifact:
    """Idempotent requirements agent Temporal activity.

    On the hot path (artifact already persisted for this version): returns
    the existing RequirementsArtifact without invoking the LangGraph graph.
    On the cold path: invokes the requirements LangGraph graph, maps the
    final state to a RequirementsArtifact, persists via write_and_notify,
    and returns the artifact.

    M10.2 (REQ-M10-03 resume half): when input.clarification_answer is set,
    resumes the SAME thread_id=str(run_id) PostgresSaver checkpoint by
    injecting only the answer as the next HumanMessage (D-M10-02 — no
    agent-logic change). After either invocation path, detect_clarification_need
    inspects final_state; if the agent ended its turn with another question,
    a ClarificationRequest is returned WITHOUT writing an artifact (Pitfall 6).
    """
    start = time.monotonic()
    status = "ok"
    try:
        # D-05: idempotency check — return early if artifact already exists
        existing = await check_existing_artifact(
            input.run_id, "requirements", input.agent_version, tenant_id=input.tenant_id
        )
        if existing is not None:
            return RequirementsArtifact(**existing)

        # Cold path — invoke the orchestrator-native requirements LangGraph graph.
        # Lazy import to avoid module-level transitive dependency failures during
        # process startup (xhtml2pdf, docxtpl, etc. are heavy optional deps).
        # Post D-02 import — orchestrator-native path; see T-M5-08 + Anti-Patterns.
        from agents_orchestrator.requirements_agent.agents.planning import (  # noqa: PLC0415
            app as _requirements_graph,
            tools as _req_tools,
            INGESTION_SYS_MESSAGE as _req_base_prompt,
        )

        # Inject the tenant's board connector for the duration of the graph run so
        # the requirements board tools (list_board_items, fetch_board_item_detail,
        # …) can pull user stories from ADO/Jira. The Redis-stream worker does the
        # same (requirements_worker.py); the Temporal activity must too, since it
        # invokes the graph directly. Skipped when no tenant (board tools then
        # fail closed via get_connector()). clear_connector() in finally releases
        # the credential reference even on error (REQ-M3-10).
        from config.connectors.context import set_connector, clear_connector  # noqa: PLC0415
        from config.connector_factory import get_connector_for_session  # noqa: PLC0415

        _connector_injected = False
        if input.tenant_id:
            try:
                _connector = await get_connector_for_session(
                    kind=stage_connector_kind(input, "requirements"),
                    tenant_id=input.tenant_id,
                )
                set_connector(_connector)
                _connector_injected = True
            except Exception:
                # Connector resolution failure must not crash the run — board tools
                # fail closed individually if they are called without a connector.
                _connector_injected = False

        # Per-run audit + token/cost tracking. The legacy WS/REST API attaches this
        # handler so tokens + cost are recorded; the Temporal path must too, or every
        # run reports zero tokens/cost. (on_llm_end → AGENT_TOKENS + cost_service.emit)
        from contextlib import ExitStack  # noqa: PLC0415

        from shared.observability import build_agent_callbacks  # noqa: PLC0415

        _callbacks, _trace_cm = build_agent_callbacks(
            run_id=str(input.run_id),
            tenant_id=input.tenant_id or "",
            agent_type="requirements",
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
                input, "requirements", base_tools=_req_tools, base_prompt=_req_base_prompt
            ) as run:
                # Run-start hard block (DP6): surface capability gap as a terminal,
                # actionable error so the workflow does not retry a broken configuration.
                if run.gap:
                    raise ApplicationError(
                        gap_message("requirements", run.gap),
                        non_retryable=True,
                    )

                # Capability-resolved profile prompt: prepend only when the profile
                # resolution produced additional instructions; avoids a no-op SystemMessage
                # on the dev/no-profile path where run.prompt equals the base prompt.
                _profile_prefix: list = (
                    [SystemMessage(content=run.prompt)] if run.prompt and run.prompt != _req_base_prompt else []
                )

                if input.clarification_answer:
                    # Resume path (M10.2): inject ONLY the answer as the next
                    # HumanMessage under the SAME thread_id so LangGraph resumes
                    # from the PostgresSaver checkpoint at the pre-question state.
                    # Do not re-send tenant_id or the initial prompt.
                    # Profile prefix is NOT injected here—it already exists in the checkpoint.
                    final_state = await _requirements_graph.ainvoke(
                        {"messages": [HumanMessage(content=input.clarification_answer)]},
                        config=_cfg,
                    )
                else:
                    prompt = (
                        f"Start requirements analysis for project_id={input.project_id}. "
                        f"trigger={input.trigger}"
                        + (f", work_item_id={input.work_item_id}" if input.work_item_id else "")
                        + "."
                    )
                    final_state = await _requirements_graph.ainvoke(
                        {"messages": [*_profile_prefix, HumanMessage(content=prompt)], "tenant_id": input.tenant_id, "model_id": input.model_id, "offering_id": input.offering_id},
                        config=_cfg,
                    )
        finally:
            _trace_stack.close()
            if _connector_injected:
                clear_connector()

        # M10.2: post-hoc inspection — if the agent ended its turn with
        # another question, return ClarificationRequest WITHOUT writing an
        # artifact (Pitfall 6).
        clarification = detect_clarification_need(final_state, str(input.run_id), "requirements")
        if clarification is not None:
            return clarification

        # Extract content from the final agent message
        messages = final_state.get("messages", [])
        brd_content: Optional[str] = None
        if messages:
            last = messages[-1]
            brd_content = getattr(last, "content", None)
            if isinstance(brd_content, list):
                # Anthropic-style content blocks
                brd_content = " ".join(
                    block.get("text", "") if isinstance(block, dict) else str(block)
                    for block in brd_content
                )

        artifact = RequirementsArtifact(
            agent_session_id=input.run_id,
            brd_content=brd_content,
            version=input.agent_version,
        )

        await write_and_notify(input.run_id, "requirements", artifact.model_dump(), tenant_id=input.tenant_id)
        return artifact

    except Exception:
        status = "error"
        raise
    finally:
        elapsed = time.monotonic() - start
        TEMPORAL_ACTIVITY_DURATION.labels(
            activity=_ACTIVITY_NAME, status=status
        ).observe(elapsed)
