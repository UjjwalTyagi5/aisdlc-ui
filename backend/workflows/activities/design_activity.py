"""Temporal activity wrapper for the design & architecture agent (M5-04, D-01).

Idempotency contract (D-05): if an artifact already exists for
(run_id, "design", agent_version), it is returned immediately —
the LangGraph graph is NOT re-invoked.

No self-timeout is declared here. start_to_close_timeout is the exclusive
responsibility of the workflow caller (plan 06, D-09) — T-M5-09 transfer.
"""
from __future__ import annotations

import json
import time

from langchain_core.messages import HumanMessage
from temporalio import activity

from shared.models.artifacts import DesignArtifact
from shared.models.workflow_models import ClarificationRequest, SDLCWorkflowInput
from shared.services.metrics import TEMPORAL_ACTIVITY_DURATION
from workflows.activities._base import (
    check_existing_artifact,
    detect_clarification_need,
    mcp_tools_for_stage,
    stage_connector_kind,
    write_and_notify,
)

_ACTIVITY_NAME = "run_design_activity"


def _build_design_prompt(project_id, trigger, work_item_id, requirements_payload):
    base = f"Generate architecture design for project_id={project_id}. trigger={trigger}"
    if work_item_id:
        base += f", work_item_id={work_item_id}"
    if requirements_payload:
        base += ("\n\nApproved requirements (consume these — do not ask the user to "
                 "re-supply them):\n" + json.dumps(requirements_payload, indent=2)[:20000])
    return base + "."


@activity.defn(name=_ACTIVITY_NAME)
async def run_design_activity(input: SDLCWorkflowInput) -> ClarificationRequest | DesignArtifact:
    """Idempotent design & architecture agent Temporal activity.

    On the hot path (artifact already persisted for this version): returns
    the existing DesignArtifact without invoking the LangGraph graph.
    On the cold path: invokes the design LangGraph graph, maps the final
    state to a DesignArtifact, persists via write_and_notify, and returns
    the artifact.

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
            input.run_id, "design", input.agent_version, tenant_id=input.tenant_id
        )
        if existing is not None:
            return DesignArtifact(**existing)

        # Cold path — lazy import to avoid transitive dependency failures at startup.
        from agents_orchestrator.design_architecture_agent.agents.architecture import (  # noqa: PLC0415
            app as _design_graph,
        )

        # Inject the tenant's board connector for the run so design tools that read
        # work items (config.connectors.context.get_connector) resolve the tenant's
        # board. Mirrors requirements_activity / the Redis worker. Fail-soft +
        # clear_connector() in finally (REQ-M3-10).
        from config.connectors.context import set_connector, clear_connector  # noqa: PLC0415
        from config.connector_factory import get_connector_for_session  # noqa: PLC0415

        _connector_injected = False
        if input.tenant_id:
            try:
                set_connector(
                    await get_connector_for_session(
                        kind=stage_connector_kind(input, "design"),
                        tenant_id=input.tenant_id,
                    )
                )
                _connector_injected = True
            except Exception:
                _connector_injected = False

        # Per-run audit + token/cost tracking + Langfuse tracing (parity with the
        # legacy WS/REST path). One pipeline trace per run: build_agent_callbacks seeds
        # the Langfuse trace from run_id so every activity of the run shares one trace.
        from contextlib import ExitStack  # noqa: PLC0415

        from shared.observability import build_agent_callbacks  # noqa: PLC0415

        _callbacks, _trace_cm = build_agent_callbacks(
            run_id=str(input.run_id),
            tenant_id=input.tenant_id or "",
            agent_type="design",
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

        from workflows.activities.pipeline_session import pipeline_session  # noqa: PLC0415

        try:
            async with pipeline_session(input, "design") as ps:
                async with mcp_tools_for_stage(input, "design"):
                    if input.clarification_answer:
                        # Resume path (M10.3): inject ONLY the answer as the next
                        # HumanMessage under the SAME thread_id so LangGraph resumes
                        # from the PostgresSaver checkpoint at the pre-question state.
                        # Do not re-send tenant_id or the initial prompt.
                        final_state = await _design_graph.ainvoke(
                            {"messages": [HumanMessage(content=input.clarification_answer)]},
                            config=_cfg,
                        )
                    else:
                        prompt = _build_design_prompt(
                            input.project_id,
                            input.trigger,
                            input.work_item_id,
                            (ps._upstream or {}).get("requirements_payload"),
                        )
                        final_state = await _design_graph.ainvoke(
                            {"messages": [HumanMessage(content=prompt)], "tenant_id": input.tenant_id, "model_id": input.model_id, "offering_id": input.offering_id},
                            config=_cfg,
                        )
        finally:
            _trace_stack.close()
            if _connector_injected:
                clear_connector()

        # M10.3: post-hoc inspection — if the agent ended its turn with
        # another question, return ClarificationRequest WITHOUT writing an
        # artifact (Pitfall 6).
        clarification = detect_clarification_need(final_state, str(input.run_id), "design")
        if clarification is not None:
            return clarification

        # Collect the full design markdown. The agent streams the document via the
        # generate_architecture* tools, so prefer the longest tool-message content;
        # fall back to the final assistant message.
        from shared.models.design import parse_artifact_sections  # noqa: PLC0415

        messages = final_state.get("messages", [])

        def _msg_text(msg) -> str:
            content = getattr(msg, "content", None)
            if isinstance(content, list):
                return " ".join(
                    block.get("text", "") if isinstance(block, dict) else str(block)
                    for block in content
                )
            return content or ""

        from langchain_core.messages import ToolMessage as _ToolMessage  # noqa: PLC0415
        tool_texts = [_msg_text(m) for m in messages if isinstance(m, _ToolMessage)]
        design_doc = max(tool_texts, key=len) if tool_texts else ""
        if not design_doc and messages:
            design_doc = _msg_text(messages[-1])

        parsed = parse_artifact_sections(design_doc)
        artifact = DesignArtifact(
            hld=parsed.hld or (design_doc if not parsed.lld else None),
            lld=parsed.lld,
            api_contracts=parsed.api_contract,
            database_schema=parsed.database_schema,
            c4_diagram_url=parsed.c4_diagrams,
            security_checklist=parsed.security_checklist,
            version=input.agent_version,
        )

        await write_and_notify(input.run_id, "design", artifact.model_dump(), tenant_id=input.tenant_id)
        return artifact

    except Exception:
        status = "error"
        raise
    finally:
        elapsed = time.monotonic() - start
        TEMPORAL_ACTIVITY_DURATION.labels(
            activity=_ACTIVITY_NAME, status=status
        ).observe(elapsed)
