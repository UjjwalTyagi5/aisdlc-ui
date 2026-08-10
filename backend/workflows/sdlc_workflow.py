"""SDLCWorkflow — Temporal @workflow.defn orchestrating the 4-phase SDLC pipeline.

Architecture decisions honoured by this file:
  D-01: Each agent phase runs as a Temporal activity (no direct LangGraph calls here).
  D-04: Single-approval HITL gates at every inter-agent boundary with SLA timers.
  D-06: Workflow body contains NO direct I/O — all DB/HTTP/Redis calls go through
        activities (sync_run_status_activity, emit_escalation_activity).
  D-07: Run status transitions (running->awaiting_approval->complete) driven by
        sync_run_status_activity calls — observable in the Postgres runs table.
        sync calls are best-effort (non-fatal): pipeline progress must never block
        on observability side-effects.
  D-09: Every execute_activity call carries an explicit start_to_close_timeout.

SLA gate implementation (RESEARCH.md Pattern 6 + Pitfall 2):
  workflow.wait_condition(fn, timeout=timedelta(...)) raises asyncio.TimeoutError
  (stdlib — NOT temporalio.exceptions.TimeoutError) when the timeout fires.
  Catch asyncio.TimeoutError specifically; temporalio.exceptions.TimeoutError is
  a different class and will NOT catch the SLA breach.

Rejection re-run (RESEARCH.md Pitfall 7 / EX-1):
  On rejection the workflow re-runs the CURRENT phase activity within the SAME
  workflow execution — do NOT use workflow.continue_as_new on rejection.
  continue_as_new is reserved for history-cap management only.

Temporal sandbox (Pitfall 1):
  Non-deterministic / sandbox-unsafe modules (sqlalchemy, asyncpg, etc.) are
  imported exclusively inside activity functions — never at workflow module level.
  Activity function references imported at module level use
  with workflow.unsafe.imports_passed_through() per Temporal Python sandbox rules.
"""
from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Optional

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError

# Activity function imports must use imports_passed_through because temporalio==1.27.2
# sandboxes workflow modules to enforce determinism.  Importing activity modules at the
# top level of a workflow file is the documented pattern for registering activity
# references without executing their side-effect-laden bodies.
with workflow.unsafe.imports_passed_through():
    from workflows.activities.emit_escalation_activity import emit_escalation_activity
    from workflows.activities.sync_status_activity import sync_run_status_activity
    from workflows.activity_dispatch import get_activity_fn
    from workflows.execution_plan import (
        ExecutionPlan,
        ExecutionPhase,
        build_execution_plan,
    )
    from shared.models.workflow_models import (
        SDLCWorkflowInput,
        HITLSignal,
        ClarificationRequest,
        ClarificationAnswer,
    )
    from config.env import (
        SLA_GRACE_MINUTES,
        SLA_CLARIFICATION_HOURS,
    )

def _as_clarification_request(result: object) -> Optional["ClarificationRequest"]:
    """Return a ClarificationRequest if `result` represents one, else None.

    Handles both shapes the default Temporal DataConverter may produce for an
    activity declared `-> RequirementsArtifact | ClarificationRequest`:
      - a ClarificationRequest INSTANCE (observed default-converter behavior:
        the dict payload is parsed against the function's return type hints), or
      - a plain dict carrying "clarification_id" (defensive fallback).
    Never inspects RequirementsArtifact instances/dicts (no clarification_id).
    """
    if isinstance(result, ClarificationRequest):
        return result
    if isinstance(result, dict) and result.get("clarification_id"):
        return ClarificationRequest(**result)
    return None


# ── retry policy applied to all agent activities (D-09) ──────────────────────
_AGENT_RETRY = RetryPolicy(
    maximum_attempts=3,
    initial_interval=timedelta(seconds=5),
    backoff_coefficient=2.0,
)

# ── activity timeout: 30 min per agent phase (D-09) ───────────────────────────
_AGENT_TIMEOUT = timedelta(minutes=30)

# ── escalation activity timeout (side-effect only; must be fast) ──────────────
_ESCALATION_TIMEOUT = timedelta(seconds=30)

# ── status-sync activity timeout (fast DB write) ─────────────────────────────
_SYNC_TIMEOUT = timedelta(seconds=15)

# ── single-attempt retry for best-effort calls (status-sync is observability) ─
_SYNC_RETRY = RetryPolicy(maximum_attempts=1)


@workflow.defn
class SDLCWorkflow:
    """Temporal workflow that iterates a data-driven ExecutionPlan of agent phases.

    The phase sequence comes from `input.execution_plan` (Task 4) — or a default
    plan built from AGENT_REGISTRY when none is supplied — instead of a hardcoded
    requirements->design->development->testing body. Each phase's gate, SLA, and
    rejection limit are read from its ExecutionPhase, so adding/reordering agents
    requires no workflow code changes.

    Each inter-agent boundary is guarded by an HITL gate backed by
    workflow.wait_condition(..., timeout=...). The SLA deadline per gate is
    phase.sla_hours; gate semantics are phase.gate_type:
      auto_approve        — no gate; proceed immediately
      approval_required   — any agent approval clears the gate
      all_must_approve    — every agent in the phase must approve (parallel phases)

    Signal contract:
      agent_approved / agent_rejected   — generic; agent_id carried in payload
      <agent>_approved / <agent>_rejected — backward-compat aliases that inject
        agent_id and delegate to the generic handlers (requirements, design,
        development, testing). Existing callers and the frontend keep working.

    Agents present in the registry but lacking a Temporal activity (e.g.
    deployment) are skipped — they neither run nor gate (activity_dispatch
    contract).
    """

    def __init__(self) -> None:
        # ── dynamic per-agent gate flags (replaces hardcoded per-phase bools) ──
        # {agent_id: {"approved": bool, "rejected": bool}}
        self._gate_flags: dict[str, dict[str, bool]] = {}
        # ── last signal received (useful for audit / debugging) ───────────────
        self._last_signal: Optional[HITLSignal] = None
        # ── within-agent clarification state (M10.2, REQ-M10-03) ──────────────
        self._clarification_answer: Optional[ClarificationAnswer] = None
        self._pending_clarification_id: Optional[str] = None

    # ── Gate-flag bookkeeping ─────────────────────────────────────────────────

    def _ensure_gate(self, agent_id: str) -> None:
        if agent_id not in self._gate_flags:
            self._gate_flags[agent_id] = {"approved": False, "rejected": False}

    # ── Generic signal handlers — any agent (agent_id carried in payload) ─────

    @workflow.signal
    async def agent_approved(self, signal: HITLSignal) -> None:
        agent_id = signal.payload.get("agent_id", "")
        self._ensure_gate(agent_id)
        self._gate_flags[agent_id]["approved"] = True
        self._last_signal = signal

    @workflow.signal
    async def agent_rejected(self, signal: HITLSignal) -> None:
        agent_id = signal.payload.get("agent_id", "")
        self._ensure_gate(agent_id)
        self._gate_flags[agent_id]["rejected"] = True
        self._last_signal = signal

    # ── Backward-compat per-phase signal aliases (frontend + existing tests) ──
    # These delegate to the generic agent_approved/agent_rejected handlers by
    # injecting the agent_id into the payload. They MUST remain so callers that
    # send `requirements_approved` (etc.) keep working after the data-driven
    # refactor.

    @workflow.signal
    async def requirements_approved(self, signal: HITLSignal) -> None:
        signal.payload["agent_id"] = "requirements"
        await self.agent_approved(signal)

    @workflow.signal
    async def requirements_rejected(self, signal: HITLSignal) -> None:
        signal.payload["agent_id"] = "requirements"
        await self.agent_rejected(signal)

    @workflow.signal
    async def design_approved(self, signal: HITLSignal) -> None:
        signal.payload["agent_id"] = "design"
        await self.agent_approved(signal)

    @workflow.signal
    async def design_rejected(self, signal: HITLSignal) -> None:
        signal.payload["agent_id"] = "design"
        await self.agent_rejected(signal)

    @workflow.signal
    async def development_approved(self, signal: HITLSignal) -> None:
        signal.payload["agent_id"] = "development"
        await self.agent_approved(signal)

    @workflow.signal
    async def development_rejected(self, signal: HITLSignal) -> None:
        signal.payload["agent_id"] = "development"
        await self.agent_rejected(signal)

    @workflow.signal
    async def testing_approved(self, signal: HITLSignal) -> None:
        signal.payload["agent_id"] = "testing"
        await self.agent_approved(signal)

    @workflow.signal
    async def testing_rejected(self, signal: HITLSignal) -> None:
        signal.payload["agent_id"] = "testing"
        await self.agent_rejected(signal)

    # ── Signal handler — within-agent clarification (M10.2, REQ-M10-03) ─────

    @workflow.signal
    async def within_agent_clarification(self, answer: ClarificationAnswer) -> None:
        """Deliver a clarification answer to a suspended agent turn.

        T-10.2-01 / T-10.2-04: only accepted when answer.clarification_id
        matches the currently pending ClarificationRequest.clarification_id —
        stale, forged, or replayed answers for a non-pending clarification are
        silently dropped.
        """
        if answer.clarification_id == self._pending_clarification_id:
            self._clarification_answer = answer

    # ── Main workflow run ─────────────────────────────────────────────────────

    @workflow.run
    async def run(self, input: SDLCWorkflowInput) -> dict:
        """Iterate an ExecutionPlan's phases, gating each per its policy.

        Data-driven replacement for the previously hardcoded 4-phase body. The
        per-phase behavior (run activity -> clarification loop -> HITL gate ->
        rejection re-run/re-gate -> SLA escalation -> status sync) is preserved
        exactly; only the phase sequence is now driven by `input.execution_plan`
        (or a default plan built from AGENT_REGISTRY when none is supplied).

        ContinueAsNew is checked at the top of each phase to cap history growth.
        No I/O is performed here — every side-effect goes through an activity.
        """
        run_id = input.run_id
        tenant_id = input.tenant_id  # threaded into sync_run_status_activity (REQ-M7-07)

        # ── Build or restore the execution plan ───────────────────────────────
        if input.execution_plan:
            plan = ExecutionPlan(**input.execution_plan)
        else:
            plan = build_execution_plan(
                run_id=run_id, project_id=input.project_id, mode="pipeline"
            )

        first_stage = plan.phases[0].agent_ids[0] if plan.phases else "complete"
        await self._sync_status(run_id, "running", first_stage, False, "run.workflow_started", tenant_id)

        phases_completed: list[str] = []

        for phase in plan.phases:
            if workflow.info().is_continue_as_new_suggested():
                workflow.continue_as_new(input)

            # Only agents with a registered activity actually run. Agents in the
            # registry without a Temporal activity (e.g. deployment) are skipped
            # rather than crashing the workflow (activity_dispatch contract).
            runnable = [aid for aid in phase.agent_ids if self._has_activity(aid)]
            if not runnable:
                continue

            primary_agent = runnable[0]
            await self._sync_status(run_id, "running", primary_agent, False, "run.activity_started", tenant_id)

            # ── Execute the agent(s) for this phase (clarification loop each) ──
            if phase.parallel and len(runnable) > 1:
                await asyncio.gather(*[
                    self._run_agent_in_phase(aid, input) for aid in runnable
                ])
            else:
                for aid in runnable:
                    await self._run_agent_in_phase(aid, input)

            phases_completed.extend(runnable)

            # ── Apply the phase gate ──────────────────────────────────────────
            if phase.gate_type == "auto_approve":
                continue

            await self._sync_status(
                run_id, f"awaiting_{primary_agent}_approval", primary_agent, True, "run.gate_reached", tenant_id
            )

            await self._wait_for_phase_approval(phase, runnable, input)

            # EX-1: rejection re-runs the phase activity (NOT continue_as_new),
            # then re-enters the gate. Bounded by phase.max_rejections.
            rejections = 0
            while self._is_phase_rejected(phase, runnable) and rejections < phase.max_rejections:
                rejections += 1
                self._clear_phase_flags(runnable)

                for aid in runnable:
                    await self._run_agent_in_phase(aid, input)

                await self._sync_status(
                    run_id, f"awaiting_{primary_agent}_approval", primary_agent, True, "run.gate_reached", tenant_id
                )
                await self._wait_for_phase_approval(phase, runnable, input)

        # ── Workflow complete ─────────────────────────────────────────────────
        await self._sync_status(run_id, "complete", "complete", False, "run.workflow_completed", tenant_id)

        return {
            "status": "complete",
            "run_id": run_id,
            "phases_completed": phases_completed,
        }

    # ── Phase gate / dispatch helpers ─────────────────────────────────────────

    def _has_activity(self, agent_id: str) -> bool:
        """True if agent_id has a Temporal activity registered (else it is skipped)."""
        try:
            get_activity_fn(agent_id)
            return True
        except KeyError:
            return False

    def _is_phase_approved(self, phase: ExecutionPhase, agent_ids: list[str]) -> bool:
        if phase.gate_type == "all_must_approve":
            return all(
                self._gate_flags.get(aid, {}).get("approved", False)
                for aid in agent_ids
            )
        return any(
            self._gate_flags.get(aid, {}).get("approved", False)
            for aid in agent_ids
        )

    def _is_phase_rejected(self, phase: ExecutionPhase, agent_ids: list[str]) -> bool:
        return any(
            self._gate_flags.get(aid, {}).get("rejected", False)
            and not self._gate_flags.get(aid, {}).get("approved", False)
            for aid in agent_ids
        )

    def _clear_phase_flags(self, agent_ids: list[str]) -> None:
        for aid in agent_ids:
            self._gate_flags[aid] = {"approved": False, "rejected": False}

    async def _wait_for_phase_approval(
        self, phase: ExecutionPhase, agent_ids: list[str], input: SDLCWorkflowInput
    ) -> None:
        for aid in agent_ids:
            self._ensure_gate(aid)
        await self._wait_for_approval_or_escalate(
            approved_flag=lambda: self._is_phase_approved(phase, agent_ids),
            rejected_flag=lambda: self._is_phase_rejected(phase, agent_ids),
            phase=agent_ids[0],
            sla_hours=phase.sla_hours,
            input=input,
        )

    async def _run_agent_in_phase(self, agent_id: str, input: SDLCWorkflowInput) -> object:
        activity_fn = get_activity_fn(agent_id)
        return await self._run_phase_with_clarification(activity_fn, input, agent_id)

    # ── Generalized within-agent clarification loop (M10.3, REQ-M10-08/09) ───

    async def _run_phase_with_clarification(
        self,
        activity_fn,
        input: SDLCWorkflowInput,
        phase: str,
    ) -> object:
        """Execute a phase activity, looping on ClarificationRequest results.

        Generalizes the requirements-phase reference loop shipped by
        milestone-10.2-01 (REQ-M10-08): every phase activity may return either
        its typed artifact or a ClarificationRequest (discriminated via
        _as_clarification_request — the default DataConverter delivers a
        ClarificationRequest INSTANCE for activities typed
        `-> X | ClarificationRequest`, dict-with-clarification_id is a
        defensive fallback).

        On a ClarificationRequest:
          - if input.max_clarification_rounds is reached, emit a final
            escalation and break, returning the last result as-is;
          - otherwise wait for a within_agent_clarification signal (or SLA
            escalation, gate stays open per _wait_for_clarification_or_escalate);
          - if no answer arrives, break and return the last result as-is;
          - else re-invoke activity_fn with clarification_answer injected and
            loop.

        RESEARCH Pitfall 5: NEVER call workflow.continue_as_new inside this
        loop — continue_as_new checks remain at phase boundaries only.

        After the loop exits (by any path), self._pending_clarification_id is
        reset to None so a stale signal for a consumed clarification cannot
        leak into the next phase's gate.
        """
        result = await workflow.execute_activity(
            activity_fn,
            input,
            start_to_close_timeout=_AGENT_TIMEOUT,
            retry_policy=_AGENT_RETRY,
        )

        rounds = 0
        clarified_input = input
        try:
            while (req := _as_clarification_request(result)) is not None:
                if rounds >= clarified_input.max_clarification_rounds:
                    # Cap reached — emit a final escalation and proceed with
                    # the last activity result as-is (do not loop forever).
                    await workflow.execute_activity(
                        emit_escalation_activity,
                        {
                            "run_id": input.run_id,
                            "phase": phase,
                            "tenant_id": input.tenant_id,
                            "grace_applied": True,
                            "elapsed_seconds": 0.0,
                        },
                        start_to_close_timeout=_ESCALATION_TIMEOUT,
                    )
                    break

                answer = await self._wait_for_clarification_or_escalate(req, clarified_input)
                if answer is None:
                    # No answer arrived even after escalation retries —
                    # proceed with the last activity result as-is.
                    break

                clarified_input = clarified_input.model_copy(update={"clarification_answer": answer.answer})
                result = await workflow.execute_activity(
                    activity_fn,
                    clarified_input,
                    start_to_close_timeout=_AGENT_TIMEOUT,
                    retry_policy=_AGENT_RETRY,
                )
                rounds += 1
        finally:
            # Reset pending-clarification correlation state so a stale signal
            # for this (now-consumed) clarification cannot leak into the
            # next phase's gate.
            self._pending_clarification_id = None

        return result

    # ── HITL gate helper ──────────────────────────────────────────────────────

    async def _wait_for_approval_or_escalate(
        self,
        approved_flag,
        rejected_flag,
        phase: str,
        sla_hours: int,
        input: SDLCWorkflowInput,
    ) -> None:
        """Block until approved/rejected, escalating on SLA breach.

        SLA deadline starts SLA_GRACE_MINUTES after gate entry (D-04).
        On asyncio.TimeoutError (Pitfall 2 — stdlib, NOT temporalio.exceptions.TimeoutError):
          1. Invoke emit_escalation_activity to record the breach in Postgres.
          2. Continue waiting for the signal — the gate does NOT close on SLA
             breach so operators can still approve/reject after escalation.

        The grace period is applied by adding SLA_GRACE_MINUTES to the SLA
        deadline: the timer fires sla_hours + grace_minutes after gate entry.
        """
        # Total wait = SLA hours + grace period (D-04).
        # workflow.now() must be used instead of datetime.now() for determinism (Pitfall 1).
        total_timeout = timedelta(hours=sla_hours, minutes=SLA_GRACE_MINUTES)
        elapsed_seconds = total_timeout.total_seconds()

        try:
            await workflow.wait_condition(
                lambda: approved_flag() or rejected_flag(),
                timeout=total_timeout,
            )
        except asyncio.TimeoutError:
            # SLA breached (Pitfall 2: catch asyncio.TimeoutError, not temporalio's).
            await workflow.execute_activity(
                emit_escalation_activity,
                {
                    "run_id": input.run_id,
                    "phase": phase,
                    "tenant_id": input.tenant_id,
                    "grace_applied": True,
                    "elapsed_seconds": elapsed_seconds,
                },
                start_to_close_timeout=_ESCALATION_TIMEOUT,
            )
            # After escalation, continue waiting so operators can still signal.
            # A second timeout here would re-escalate — acceptable for MVP scope.
            try:
                await workflow.wait_condition(
                    lambda: approved_flag() or rejected_flag(),
                    timeout=total_timeout,
                )
            except asyncio.TimeoutError:
                # Second SLA breach — emit another escalation and give up waiting.
                # The workflow proceeds to the next phase to avoid permanent stalls
                # when no operator responds.
                await workflow.execute_activity(
                    emit_escalation_activity,
                    {
                        "run_id": input.run_id,
                        "phase": phase,
                        "tenant_id": input.tenant_id,
                        "grace_applied": True,
                        "elapsed_seconds": elapsed_seconds * 2,
                    },
                    start_to_close_timeout=_ESCALATION_TIMEOUT,
                )

    # ── Within-agent clarification wait helper (M10.2, REQ-M10-03/04) ─────────

    async def _wait_for_clarification_or_escalate(
        self,
        req: ClarificationRequest,
        input: SDLCWorkflowInput,
    ) -> Optional[ClarificationAnswer]:
        """Suspend until a matching within_agent_clarification signal arrives.

        Adapted (verbatim in structure) from _wait_for_approval_or_escalate:
        on asyncio.TimeoutError (Pitfall 1/2 — stdlib, NOT
        temporalio.exceptions.TimeoutError) emit emit_escalation_activity and
        RE-ENTER the wait — the gate stays open for a late answer (D-M10-04 /
        T-10.2-02). Returns None only if no answer arrives after both waits;
        the caller treats None as "proceed without further clarification".
        """
        self._pending_clarification_id = req.clarification_id
        self._clarification_answer = None

        await self._sync_status(
            input.run_id, "awaiting_clarification", req.phase, True, "run.clarification_requested", input.tenant_id
        )

        total_timeout = timedelta(hours=SLA_CLARIFICATION_HOURS, minutes=SLA_GRACE_MINUTES)
        elapsed_seconds = total_timeout.total_seconds()

        try:
            await workflow.wait_condition(
                lambda: self._clarification_answer is not None,
                timeout=total_timeout,
            )
        except asyncio.TimeoutError:
            # SLA breached — escalate but keep the gate open (T-10.2-02).
            await workflow.execute_activity(
                emit_escalation_activity,
                {
                    "run_id": input.run_id,
                    "phase": req.phase,
                    "tenant_id": input.tenant_id,
                    "grace_applied": True,
                    "elapsed_seconds": elapsed_seconds,
                },
                start_to_close_timeout=_ESCALATION_TIMEOUT,
            )
            try:
                await workflow.wait_condition(
                    lambda: self._clarification_answer is not None,
                    timeout=total_timeout,
                )
            except asyncio.TimeoutError:
                # Second SLA breach — emit one more escalation and return
                # whatever answer (possibly None) we have; the caller decides
                # whether to proceed without it.
                await workflow.execute_activity(
                    emit_escalation_activity,
                    {
                        "run_id": input.run_id,
                        "phase": req.phase,
                        "tenant_id": input.tenant_id,
                        "grace_applied": True,
                        "elapsed_seconds": elapsed_seconds * 2,
                    },
                    start_to_close_timeout=_ESCALATION_TIMEOUT,
                )

        return self._clarification_answer

    # ── Best-effort status sync helper ────────────────────────────────────────

    async def _sync_status(
        self,
        run_id: str,
        status: str,
        stage: str,
        gate_pending: bool,
        event_type: Optional[str] = None,
        tenant_id: Optional[str] = None,
    ) -> None:
        """Invoke sync_run_status_activity with a single attempt; swallow errors.

        Status sync is observability-only (D-07).  If sync_run_status_activity is
        not registered on the current worker (e.g. in unit tests that register a
        minimal activity set), the pipeline must not be blocked.  ActivityError is
        caught and silently discarded — the Temporal workflow history retains the
        full state; the Postgres mirror is the secondary view.

        tenant_id is passed through to sync_run_status_activity so the activity
        can scope its DB session to the correct tenant (REQ-M7-07 / D-04).
        Contextvars do not cross Temporal activity boundaries (PITFALLS Integration
        Gotchas) — explicit arg is required.
        """
        try:
            await workflow.execute_activity(
                sync_run_status_activity,
                args=[run_id, status, stage, gate_pending, event_type, tenant_id],
                start_to_close_timeout=_SYNC_TIMEOUT,
                retry_policy=_SYNC_RETRY,
            )
        except ActivityError:
            # Tolerate missing or failed sync — observability must not block the pipeline.
            pass
