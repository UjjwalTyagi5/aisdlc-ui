"""REQ-M10-08/09: cross-phase within-agent clarification suspend -> signal -> resume.

Tier: unit (time-skipping — no real Temporal server, no LLM, no Postgres).

milestone-10.2-01 wired the suspend->signal->resume clarification loop ONLY
into the requirements phase as a reference (test_clarification_workflow.py).
milestone-10.3-01 generalized that loop into _run_phase_with_clarification and
applied it to design, development, AND testing. This module proves the
generalization: design, development, and testing each suspend on a
ClarificationRequest returned by their activity, release ONLY on a
within_agent_clarification signal whose clarification_id matches the pending
request (T-10.2-01/04 correlation guard, exercised across phases), and
re-invoke the activity with clarification_answer injected so the run advances
past that phase's clarification gate.

All activities are MOCKED (registered under the real activity names) so the
test is fully autonomous — DLT-10 owns live worker-restart durability.
"""
from __future__ import annotations

import asyncio
import uuid

import pytest

from tests.temporal.conftest import make_run_id, make_workflow_input

try:
    from temporalio import activity
    from temporalio.testing import WorkflowEnvironment
    from temporalio.worker import Worker
    _TEMPORALIO_AVAILABLE = True
except ImportError:
    _TEMPORALIO_AVAILABLE = False

_skip_no_temporal = pytest.mark.skipif(
    not _TEMPORALIO_AVAILABLE,
    reason="temporalio not installed",
)


if _TEMPORALIO_AVAILABLE:
    from shared.models.workflow_models import ClarificationAnswer, ClarificationRequest

    # Each phase under test gets its own fixed clarification_id so the test
    # can construct a matching ClarificationAnswer without querying workflow
    # state, and so a non-matching id (drawn from a different phase's id, or
    # a fresh uuid4) is guaranteed not to collide.
    _DESIGN_CLARIFICATION_ID = str(uuid.uuid4())
    _DEVELOPMENT_CLARIFICATION_ID = str(uuid.uuid4())
    _TESTING_CLARIFICATION_ID = str(uuid.uuid4())

    # Per-phase call counters — module-level so each mock can tell its first
    # call (return ClarificationRequest) from its second (return artifact).
    _design_calls = {"n": 0}
    _development_calls = {"n": 0}
    _testing_calls = {"n": 0}

    def _reset_call_counters() -> None:
        _design_calls["n"] = 0
        _development_calls["n"] = 0
        _testing_calls["n"] = 0

    @activity.defn(name="run_requirements_activity")
    async def mock_requirements_activity(input):
        """Always returns a RequirementsArtifact dict immediately — the
        requirements phase is not under test here (covered by
        test_clarification_workflow.py)."""
        return {
            "agent_session_id": input["run_id"],
            "brd_content": "BRD ready.",
            "version": input.get("agent_version", 1),
        }

    @activity.defn(name="run_design_activity")
    async def mock_design_activity(input):
        """First call: ClarificationRequest. Second call (clarification_answer
        set): DesignArtifact dict."""
        _design_calls["n"] += 1
        if input.get("clarification_answer"):
            return {"hld": f"HLD finalized after: {input['clarification_answer']}", "version": input.get("agent_version", 1)}
        return ClarificationRequest(
            questions=["Which cloud provider should the design target?"],
            thread_id=input["run_id"],
            agent_type="design",
            phase="design",
            clarification_id=_DESIGN_CLARIFICATION_ID,
        ).model_dump()

    @activity.defn(name="run_development_activity")
    async def mock_development_activity(input):
        """First call: ClarificationRequest. Second call (clarification_answer
        set): DevelopmentArtifact dict."""
        _development_calls["n"] += 1
        if input.get("clarification_answer"):
            return {
                "code_summary": f"Implementation finalized after: {input['clarification_answer']}",
                "version": input.get("agent_version", 1),
            }
        return ClarificationRequest(
            questions=["Which package manager should be used?"],
            thread_id=input["run_id"],
            agent_type="development",
            phase="development",
            clarification_id=_DEVELOPMENT_CLARIFICATION_ID,
        ).model_dump()

    @activity.defn(name="run_testing_activity")
    async def mock_testing_activity(input):
        """First call: ClarificationRequest. Second call (clarification_answer
        set): TestingArtifact dict."""
        _testing_calls["n"] += 1
        if input.get("clarification_answer"):
            return {
                "test_plan": f"Test plan finalized after: {input['clarification_answer']}",
                "version": input.get("agent_version", 1),
            }
        return ClarificationRequest(
            questions=["Should integration tests run against staging or a mock backend?"],
            thread_id=input["run_id"],
            agent_type="testing",
            phase="testing",
            clarification_id=_TESTING_CLARIFICATION_ID,
        ).model_dump()

    @activity.defn(name="run_code_review_activity")
    async def mock_code_review_activity(input):
        return {"review_summary": "Code review OK.", "version": input.get("agent_version", 1)}

    @activity.defn(name="run_security_activity")
    async def mock_security_activity(input):
        return {"scan_summary": "No findings.", "version": input.get("agent_version", 1)}

    @activity.defn(name="emit_escalation_activity")
    async def mock_emit_escalation_activity(payload):
        return None

    @activity.defn(name="sync_run_status_activity")
    async def mock_sync_run_status_activity(*args, **kwargs):
        return None

    _ALL_MOCK_ACTIVITIES = [
        mock_requirements_activity,
        mock_design_activity,
        mock_development_activity,
        mock_testing_activity,
        mock_code_review_activity,
        mock_security_activity,
        mock_emit_escalation_activity,
        mock_sync_run_status_activity,
    ]


def _approval_signal():
    from shared.models.workflow_models import HITLSignal

    return HITLSignal(actor_id="test-user", payload={}, idempotency_key=str(uuid.uuid4()))


async def _wait_until(predicate, timeout: float = 10.0, interval: float = 0.05) -> None:
    """Poll `predicate()` until truthy or `timeout` (wall-clock) seconds elapse.

    The time-skipping environment auto-advances workflow.wait_condition
    timers, but workflow-task processing still happens on real asyncio ticks
    (signals/activity completions are delivered asynchronously). Polling here
    lets the workflow task run to the point where a mock activity has been
    invoked before the test asserts on its call counter.
    """
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(interval)
    raise AssertionError(f"condition not met within {timeout}s")


@pytest.mark.unit
@_skip_no_temporal
async def test_design_phase_clarification_suspend_signal_resume(temporal_time_skip_env):
    """Design phase suspends on a ClarificationRequest, resumes on a matching
    within_agent_clarification signal, and the design activity is re-invoked
    with clarification_answer set (REQ-M10-08)."""
    from workflows.sdlc_workflow import SDLCWorkflow

    _reset_call_counters()
    env = temporal_time_skip_env
    run_id = make_run_id()
    workflow_input = make_workflow_input(run_id=run_id)

    async with Worker(
        env.client,
        task_queue="test-clarification-design-queue",
        workflows=[SDLCWorkflow],
        activities=_ALL_MOCK_ACTIVITIES,
    ):
        handle = await env.client.start_workflow(
            SDLCWorkflow.run,
            workflow_input,
            id=f"clarification-design-test-{run_id}",
            task_queue="test-clarification-design-queue",
        )

        # Advance past requirements so the workflow reaches the design phase
        # and suspends on the design ClarificationRequest.
        await handle.signal(SDLCWorkflow.requirements_approved, _approval_signal())

        # Wait for the workflow task to reach the design phase and invoke the
        # design activity (which returns a ClarificationRequest on call 1).
        await _wait_until(lambda: _design_calls["n"] >= 1)

        # A non-matching clarification_id must not release the design gate
        # (correlation guard holds across phases).
        await handle.signal(
            SDLCWorkflow.within_agent_clarification,
            ClarificationAnswer(
                clarification_id=str(uuid.uuid4()),
                answer="Wrong answer for the wrong question.",
                actor_id="attacker",
            ),
        )
        desc = await handle.describe()
        assert desc.status.name == "RUNNING"
        assert _design_calls["n"] == 1, "design activity must not be re-invoked by a non-matching signal"

        # Matching clarification_id releases the design gate.
        await handle.signal(
            SDLCWorkflow.within_agent_clarification,
            ClarificationAnswer(
                clarification_id=_DESIGN_CLARIFICATION_ID,
                answer="Target Azure.",
                actor_id="test-user",
            ),
        )

        # Approve remaining gates so the workflow runs to completion without
        # waiting on the 24h+ SLA timers.
        await handle.signal(SDLCWorkflow.design_approved, _approval_signal())
        await handle.signal(SDLCWorkflow.development_approved, _approval_signal())

        result = await handle.result()

        assert result["status"] == "complete"
        assert _design_calls["n"] == 2, "design activity must be re-invoked with the clarification answer"


@pytest.mark.unit
@_skip_no_temporal
async def test_development_phase_clarification_suspend_signal_resume(temporal_time_skip_env):
    """Development phase suspends on a ClarificationRequest, resumes on a
    matching within_agent_clarification signal, and the development activity
    is re-invoked with clarification_answer set (REQ-M10-08)."""
    from workflows.sdlc_workflow import SDLCWorkflow

    _reset_call_counters()
    env = temporal_time_skip_env
    run_id = make_run_id()
    workflow_input = make_workflow_input(run_id=run_id)

    async with Worker(
        env.client,
        task_queue="test-clarification-development-queue",
        workflows=[SDLCWorkflow],
        activities=_ALL_MOCK_ACTIVITIES,
    ):
        handle = await env.client.start_workflow(
            SDLCWorkflow.run,
            workflow_input,
            id=f"clarification-development-test-{run_id}",
            task_queue="test-clarification-development-queue",
        )

        # Advance past requirements and design (no clarification this run for
        # design — the mock returns its ClarificationRequest only on the
        # FIRST call, so without an answer the design phase will simply loop
        # to its rounds cap; instead approve design's gate is unreachable
        # until the design clarification resolves. Resolve it immediately so
        # the workflow proceeds to development.)
        await handle.signal(SDLCWorkflow.requirements_approved, _approval_signal())
        await _wait_until(lambda: _design_calls["n"] >= 1)
        await handle.signal(
            SDLCWorkflow.within_agent_clarification,
            ClarificationAnswer(
                clarification_id=_DESIGN_CLARIFICATION_ID,
                answer="Target Azure.",
                actor_id="test-user",
            ),
        )
        await _wait_until(lambda: _design_calls["n"] >= 2)
        await handle.signal(SDLCWorkflow.design_approved, _approval_signal())

        # Now the workflow reaches development and suspends on its
        # ClarificationRequest.
        await _wait_until(lambda: _development_calls["n"] >= 1)

        # A non-matching clarification_id must not release the development gate.
        await handle.signal(
            SDLCWorkflow.within_agent_clarification,
            ClarificationAnswer(
                clarification_id=str(uuid.uuid4()),
                answer="Wrong answer for the wrong question.",
                actor_id="attacker",
            ),
        )
        desc = await handle.describe()
        assert desc.status.name == "RUNNING"
        assert _development_calls["n"] == 1, "development activity must not be re-invoked by a non-matching signal"

        # Matching clarification_id releases the development gate.
        await handle.signal(
            SDLCWorkflow.within_agent_clarification,
            ClarificationAnswer(
                clarification_id=_DEVELOPMENT_CLARIFICATION_ID,
                answer="Use uv.",
                actor_id="test-user",
            ),
        )
        await handle.signal(SDLCWorkflow.development_approved, _approval_signal())

        result = await handle.result()

        assert result["status"] == "complete"
        assert _development_calls["n"] == 2, "development activity must be re-invoked with the clarification answer"


@pytest.mark.unit
@_skip_no_temporal
async def test_testing_phase_clarification_suspend_signal_resume(temporal_time_skip_env):
    """Testing phase (terminal, no downstream approval gate) suspends on a
    ClarificationRequest, resumes on a matching within_agent_clarification
    signal, and the testing activity is re-invoked with clarification_answer
    set — proving the generalized loop applies to the terminal phase too
    (REQ-M10-08)."""
    from workflows.sdlc_workflow import SDLCWorkflow

    _reset_call_counters()
    env = temporal_time_skip_env
    run_id = make_run_id()
    workflow_input = make_workflow_input(run_id=run_id)

    async with Worker(
        env.client,
        task_queue="test-clarification-testing-queue",
        workflows=[SDLCWorkflow],
        activities=_ALL_MOCK_ACTIVITIES,
    ):
        handle = await env.client.start_workflow(
            SDLCWorkflow.run,
            workflow_input,
            id=f"clarification-testing-test-{run_id}",
            task_queue="test-clarification-testing-queue",
        )

        # Advance through requirements -> design -> development, resolving
        # each phase's clarification immediately so the workflow reaches the
        # terminal testing phase.
        await handle.signal(SDLCWorkflow.requirements_approved, _approval_signal())
        await _wait_until(lambda: _design_calls["n"] >= 1)
        await handle.signal(
            SDLCWorkflow.within_agent_clarification,
            ClarificationAnswer(
                clarification_id=_DESIGN_CLARIFICATION_ID,
                answer="Target Azure.",
                actor_id="test-user",
            ),
        )
        await _wait_until(lambda: _design_calls["n"] >= 2)
        await handle.signal(SDLCWorkflow.design_approved, _approval_signal())

        await _wait_until(lambda: _development_calls["n"] >= 1)
        await handle.signal(
            SDLCWorkflow.within_agent_clarification,
            ClarificationAnswer(
                clarification_id=_DEVELOPMENT_CLARIFICATION_ID,
                answer="Use uv.",
                actor_id="test-user",
            ),
        )
        await _wait_until(lambda: _development_calls["n"] >= 2)
        await handle.signal(SDLCWorkflow.development_approved, _approval_signal())

        # Now the workflow reaches testing and suspends on its
        # ClarificationRequest.
        await _wait_until(lambda: _testing_calls["n"] >= 1)

        # A non-matching clarification_id must not release the testing gate.
        await handle.signal(
            SDLCWorkflow.within_agent_clarification,
            ClarificationAnswer(
                clarification_id=str(uuid.uuid4()),
                answer="Wrong answer for the wrong question.",
                actor_id="attacker",
            ),
        )
        desc = await handle.describe()
        assert desc.status.name == "RUNNING"
        assert _testing_calls["n"] == 1, "testing activity must not be re-invoked by a non-matching signal"

        # Matching clarification_id releases the testing gate.
        await handle.signal(
            SDLCWorkflow.within_agent_clarification,
            ClarificationAnswer(
                clarification_id=_TESTING_CLARIFICATION_ID,
                answer="Run against a mock backend.",
                actor_id="test-user",
            ),
        )

        result = await handle.result()

        assert result["status"] == "complete"
        assert _testing_calls["n"] == 2, "testing activity must be re-invoked with the clarification answer"
