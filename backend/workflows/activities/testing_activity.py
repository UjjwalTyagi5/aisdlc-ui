"""Temporal activity wrapper for the testing agent (M5-05, D-01).

Idempotency contract (D-05): if an artifact already exists for
(run_id, "testing", agent_version), it is returned immediately —
run_super_agent_async is NOT re-invoked.

Session isolation (T-M5-10 / Pitfall 5): the testing agent has module-level
shared state in testing_agent/config/shared.py (set_session_id, _TOKEN_COUNTS).
The activity ALWAYS passes session_id=str(input.run_id) to run_super_agent_async
so each Temporal activity invocation gets its own context-var scope.  This is the
SC-08 canary activity and the SC-09 concurrent-isolation requirement.

No self-timeout is declared here. start_to_close_timeout is the exclusive
responsibility of the workflow caller (plan 06, D-09) — T-M5-09 transfer.
"""
from __future__ import annotations

import time
from typing import Optional

from temporalio import activity

from shared.models.artifacts import TestingArtifact
from shared.models.workflow_models import ClarificationRequest, SDLCWorkflowInput
from shared.services.metrics import TEMPORAL_ACTIVITY_DURATION
from workflows.activities._base import (
    check_existing_artifact,
    detect_clarification_need,
    mcp_tools_for_stage,
    write_and_notify,
)

_ACTIVITY_NAME = "run_testing_activity"


@activity.defn(name=_ACTIVITY_NAME)
async def run_testing_activity(input: SDLCWorkflowInput) -> ClarificationRequest | TestingArtifact:
    """Idempotent testing agent Temporal activity (SC-08 canary).

    On the hot path (artifact already persisted for this version): returns
    the existing TestingArtifact without invoking the testing agent.
    On the cold path: invokes run_super_agent_async with session_id=str(input.run_id)
    for per-invocation session isolation (T-M5-10 / Pitfall 5), maps final_state
    to a TestingArtifact, persists via write_and_notify, and returns the artifact.

    The session_id=run_id binding ensures that concurrent Temporal activities with
    different run_ids do NOT share the testing agent's module-level contextvar state
    (SC-09 concurrency non-contamination requirement).

    M10.3 (REQ-M10-08, best-effort): when input.clarification_answer is set,
    the answer is fed through run_super_agent_async as the prompt (resume),
    keeping session_id=str(run_id) for contextvar isolation (D-M10-02). After
    either invocation, detect_clarification_need inspects final_state — the
    testing agent surfaces structured `final_outputs` rather than message-shaped
    questions, so a None return (proceed to artifact build) is the dominant
    path; a ClarificationRequest is returned without writing an artifact only
    in the rare case the trailing message is question-shaped.
    """
    start = time.monotonic()
    status = "ok"
    _trace_stack = None
    try:
        # D-05: idempotency check — return early if artifact already exists
        existing = await check_existing_artifact(
            input.run_id, "testing", input.agent_version, tenant_id=input.tenant_id
        )
        if existing is not None:
            return TestingArtifact(**existing)

        # Cold path — lazy import to avoid module-level transitive dependency failures
        # during process startup (xhtml2pdf, docxtpl, heavy deps in testing agent tree).
        # Must import run_super_agent_async (async entrypoint) — NEVER run_super_agent
        # (sync) which would block the Temporal activity event loop.
        from agents_orchestrator.testing_agent.agents.testing_agent import (  # noqa: PLC0415
            run_super_agent_async,
        )

        # Per-run audit/cost + Langfuse tracing, seeded from run_id so the testing
        # phase joins the same pipeline trace as the other agents. Threaded into the
        # super-agent graph via run_super_agent_async(callbacks=...).
        from contextlib import ExitStack  # noqa: PLC0415

        from shared.observability import build_agent_callbacks  # noqa: PLC0415

        _callbacks, _trace_cm = build_agent_callbacks(
            run_id=str(input.run_id),
            tenant_id=input.tenant_id or "",
            agent_type="testing",
            model=input.model_id,
            offering_id=input.offering_id,
            project_id=input.project_id,
        )
        _trace_stack = ExitStack()
        _trace_stack.enter_context(_trace_cm)

        # session_id=str(input.run_id) is the critical isolation binding (Pitfall 5).
        # Each Temporal activity invocation gets its own contextvar scope in the
        # testing agent's set_session_id() — prevents cross-contamination when
        # multiple activities run concurrently (SC-09).
        # Mirror run-keyed upstream into agent_sessions so the testing agent's
        # fetch_session_artifacts(run_id) call finds the pipeline context written
        # by the requirements/design/development activities (runs table → agent_sessions).
        # The mirror is idempotent: safe to call once before the branch.
        from workflows.activities.pipeline_session import _read_run_upstream  # noqa: PLC0415
        from shared.services.agent_session_store import upsert_agent_session  # noqa: PLC0415
        _up = await _read_run_upstream(str(input.run_id))
        await upsert_agent_session(
            str(input.run_id), agent_type="testing", tenant_id=input.tenant_id,
            current_stage="testing",
            **{k: v for k, v in _up.items() if v is not None},
        )

        async with mcp_tools_for_stage(input, "testing"):
            if input.clarification_answer:
                # Resume path (M10.3): feed the answer through the testing
                # entrypoint as the prompt, keeping session_id=str(run_id) so
                # the per-invocation contextvar isolation is preserved on resume.
                final_state = await run_super_agent_async(
                    user_prompt=input.clarification_answer,
                    input_file_path=None,
                    session_id=str(input.run_id),
                    tenant_id=input.tenant_id,
                    model_id=input.model_id,
                    offering_id=input.offering_id,
                    callbacks=_callbacks,
                )
            else:
                # Derive user_prompt from the workflow input — the testing agent
                # operates on the existing run artifacts already persisted in
                # Postgres; a minimal prompt is sufficient to trigger analysis.
                prompt: Optional[str] = (
                    f"Execute testing pipeline for project_id={input.project_id}. "
                    f"run_id={input.run_id}"
                    + (f", work_item_id={input.work_item_id}" if input.work_item_id else "")
                    + "."
                )
                final_state = await run_super_agent_async(
                    user_prompt=prompt,
                    input_file_path=None,
                    session_id=str(input.run_id),
                    tenant_id=input.tenant_id,
                    model_id=input.model_id,
                    offering_id=input.offering_id,
                    callbacks=_callbacks,
                )

        # M10.3 (best-effort): the testing agent surfaces structured
        # `final_outputs` rather than message-shaped questions, so
        # detect_clarification_need normally returns None here and the
        # existing artifact build below runs unchanged. Only when the
        # trailing message is question-shaped (rare) does this return a
        # ClarificationRequest WITHOUT writing an artifact (Pitfall 6).
        clarification = detect_clarification_need(final_state, str(input.run_id), "testing")
        if clarification is not None:
            return clarification

        # Extract per-field outputs from final_state["final_outputs"].
        # The testing agent stores structured outputs in this dict; individual
        # fields may be absent if the agent did not reach that pipeline stage.
        outputs: dict = final_state.get("final_outputs") or {}

        test_plan_content: Optional[str] = (
            final_state.get("test_plan") or outputs.get("test_plan")
        )
        # Normalize: test_plan may be a Pydantic model or plain dict from the agent
        if hasattr(test_plan_content, "model_dump"):
            test_plan_content = str(test_plan_content.model_dump())
        elif isinstance(test_plan_content, dict):
            test_plan_content = str(test_plan_content)

        test_cases: Optional[list] = outputs.get("test_cases")
        coverage_report: Optional[dict] = outputs.get("coverage_report") or (
            final_state.get("test_execution_summary")
        )
        defect_log: Optional[list] = outputs.get("defect_log")

        artifact = TestingArtifact(
            test_plan=test_plan_content,
            test_cases=test_cases,
            coverage_report=coverage_report,
            defect_log=defect_log,
            version=input.agent_version,
        )

        # Persist artifact and signal downstream consumers (D-05, D-07).
        # The agent_session_id is stored in the persisted dict to support SC-09
        # cross-contamination detection — each artifact must carry its own run_id.
        artifact_dict = artifact.model_dump()
        artifact_dict["agent_session_id"] = input.run_id
        await write_and_notify(input.run_id, "testing", artifact_dict, tenant_id=input.tenant_id)
        return artifact

    except Exception:
        status = "error"
        raise
    finally:
        if _trace_stack is not None:
            _trace_stack.close()
        elapsed = time.monotonic() - start
        TEMPORAL_ACTIVITY_DURATION.labels(
            activity=_ACTIVITY_NAME, status=status
        ).observe(elapsed)
