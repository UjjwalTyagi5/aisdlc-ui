"""AuditCallbackHandler — LangGraph BaseCallbackHandler for audit event emission (D-01).

Intercepts tool start/end and LLM end events emitted by LangGraph during agent execution
and fires AuditEventPayload records through AuditEventService.

CRITICAL design notes:
  - BOTH sync and async callback variants are implemented (RESEARCH Pitfall 1 /
    Assumptions Log A1). LangGraph astream() dispatches ONLY async variants
    (aon_tool_start, aon_tool_end, aon_llm_end) for async graph execution. An
    implementation with sync-only overrides will receive zero events from astream.
  - Sync variants schedule BOTH audit and cost emits via _fire() (create_task when a
    running loop exists, coro.close() otherwise) to avoid "coroutine never awaited"
    RuntimeWarning and guarantee no silent discard on the async-test path.
  - Async variants await both AuditEventService.emit and CostLogService.emit directly
    -- they do NOT delegate to their sync counterparts for the emit calls.
  - Attach per-invocation via config["callbacks"] = [handler] -- NOT at compile time,
    because run_id and tenant_id are unknown at import time (RESEARCH Open Q2).
  - D-01: zero changes to individual tool function definitions.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any, Optional

from langchain_core.callbacks.base import BaseCallbackHandler

from shared.audit.models import AuditEventPayload
from shared.cost.service import cost_service as _default_cost_service
from shared.services.metrics import AGENT_ERRORS, AGENT_REQUEST_LATENCY, AGENT_TOKENS


class AuditCallbackHandler(BaseCallbackHandler):
    """Intercepts LangGraph tool + LLM events and emits AuditEvents (D-01).

    Instantiated per-run so every emitted event is tenant-scoped and tied to
    the specific invocation's run_id. The service (audit_service singleton) is
    injected so tests can pass a mock without patching the module.
    """

    def __init__(
        self,
        audit_service: Any,
        run_id: str,
        tenant_id: str,
        cost_service: Any = None,
    ) -> None:
        super().__init__()
        self._svc = audit_service
        self._run_id = run_id
        self._tenant_id = tenant_id
        self._cost_service = cost_service if cost_service is not None else _default_cost_service
        # Tracks in-flight tool calls keyed by str(run_id uuid)
        self._pending_tool: dict[str, dict] = {}

    def _fire(self, coro) -> None:
        """Schedule an async coroutine from a sync context without blocking.

        If a running event loop exists (e.g., inside an async test or Temporal
        worker), create_task schedules the coroutine on that loop.  In a
        pure-sync context (no running loop) the coroutine is closed immediately
        to suppress "coroutine never awaited" RuntimeWarning -- the emit is
        silently skipped, which is acceptable (degraded, not broken).
        """
        try:
            asyncio.get_running_loop().create_task(coro)
        except RuntimeError:
            coro.close()  # no running loop; avoid RuntimeWarning
        except Exception:
            pass

    # -- Sync variants (invoked for sync graph execution / planning_app.invoke) --

    def on_tool_start(
        self,
        serialized: dict[str, Any],
        input_str: str,
        *,
        run_id: uuid.UUID,
        **kwargs: Any,
    ) -> None:
        """Record tool start; schedule tool_call_start audit event via _fire."""
        tool_name = serialized.get("name", "unknown") if serialized else "unknown"
        pending = {
            "tool_name": tool_name,
            "input": str(input_str)[:500],
            "_t0": time.monotonic(),
        }
        self._pending_tool[str(run_id)] = pending
        agent_type = kwargs.get("agent_type", "agent")
        self._fire(
            self._svc.emit(
                AuditEventPayload(
                    tenant_id=self._tenant_id,
                    run_id=self._run_id,
                    event_type="tool_call_start",
                    agent_type=agent_type,
                    actor_id=f"system:{agent_type}",
                    payload={k: v for k, v in pending.items() if k != "_t0"},
                )
            )
        )

    def on_tool_end(
        self,
        output: str,
        *,
        run_id: uuid.UUID,
        **kwargs: Any,
    ) -> None:
        """Record tool completion; schedule tool_call_end audit event via _fire."""
        pending = self._pending_tool.pop(str(run_id), {})
        agent_type = kwargs.get("agent_type", "agent")
        t0 = pending.get("_t0")
        if t0 is not None:
            try:
                AGENT_REQUEST_LATENCY.labels(agent_type=agent_type).observe(
                    time.monotonic() - t0
                )
            except Exception:
                pass
        audit_payload = {k: v for k, v in pending.items() if k != "_t0"}
        self._fire(
            self._svc.emit(
                AuditEventPayload(
                    tenant_id=self._tenant_id,
                    run_id=self._run_id,
                    event_type="tool_call_end",
                    agent_type=agent_type,
                    actor_id=f"system:{agent_type}",
                    payload={**audit_payload, "output_snippet": str(output)[:200]},
                )
            )
        )

    def _build_llm_audit_payload(
        self,
        response: Any,
        agent_type: str,
    ) -> tuple[AuditEventPayload, dict[str, Any]]:
        """Build the model_call AuditEventPayload and compute cost kwargs.

        Returns (audit_payload, cost_kwargs) so callers choose whether to
        _fire (sync path) or await (async path) the audit emit.  Also records
        Prometheus token metrics as a side effect.
        """
        llm_output = getattr(response, "llm_output", {}) or {}
        audit_payload = AuditEventPayload(
            tenant_id=self._tenant_id,
            run_id=self._run_id,
            event_type="model_call",
            agent_type=agent_type,
            actor_id=f"system:{agent_type}",
            payload={
                "model": llm_output.get("model_name", "unknown"),
                "usage": str(llm_output.get("token_usage", {})),
            },
        )
        try:
            token_usage = llm_output.get("token_usage") or {}
            model = llm_output.get("model_name", "unknown")
            input_tokens = token_usage.get("input_tokens", token_usage.get("prompt_tokens", 0)) or 0
            output_tokens = token_usage.get("output_tokens", token_usage.get("completion_tokens", 0)) or 0
            if input_tokens > 0:
                AGENT_TOKENS.labels(agent_type=agent_type, model=model, kind="input").inc(input_tokens)
            if output_tokens > 0:
                AGENT_TOKENS.labels(agent_type=agent_type, model=model, kind="output").inc(output_tokens)
        except Exception:
            pass

        # Recompute for cost kwargs (kept separate so Prometheus failure does not
        # affect the returned dict)
        token_usage = llm_output.get("token_usage") or {}
        model = llm_output.get("model_name") or "unknown"
        input_tokens = token_usage.get("input_tokens", token_usage.get("prompt_tokens", 0)) or 0
        output_tokens = token_usage.get("output_tokens", token_usage.get("completion_tokens", 0)) or 0
        cost_kwargs = dict(
            tenant_id=self._tenant_id,
            run_id=self._run_id,
            agent_type=agent_type,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            duration_ms=None,
        )
        return audit_payload, cost_kwargs

    def _record_llm_audit_and_metrics(
        self,
        response: Any,
        agent_type: str,
    ) -> dict[str, Any]:
        """Schedule the model_call audit event via _fire and record Prometheus metrics.

        Returns the cost kwargs dict so callers (sync and async) can pass it
        directly to CostLogService.emit without recomputing token counts.

        NOTE: audit emit is scheduled via _fire (not awaited) -- this method is
        sync and must be safe to call from both sync and async contexts.
        Async callers (aon_llm_end) should use _build_llm_audit_payload directly
        and await the emit themselves for a guaranteed write.
        """
        audit_payload, cost_kwargs = self._build_llm_audit_payload(response, agent_type)
        self._fire(self._svc.emit(audit_payload))
        return cost_kwargs

    def on_llm_end(
        self,
        response: Any,
        *,
        run_id: uuid.UUID,
        **kwargs: Any,
    ) -> None:
        """Record LLM completion; schedule audit + cost emits safely from sync context."""
        agent_type = kwargs.get("agent_type", "agent")
        cost_kwargs = self._record_llm_audit_and_metrics(response, agent_type)

        # REQ-M9-05/M9-06: CostLogService.emit is async; schedule safely from a
        # sync context.  If a running loop exists (e.g., called from sync code inside
        # an async test or Temporal worker), create_task fires the coroutine on that
        # loop.  In a pure-sync context (no running loop) we close the coroutine
        # immediately to suppress the "coroutine never awaited" RuntimeWarning -- the
        # cost row is silently skipped, which is acceptable (degraded, not broken).
        self._fire(self._cost_service.emit(**cost_kwargs))

    def on_tool_error(
        self,
        error: BaseException,
        *,
        run_id: uuid.UUID,
        **kwargs: Any,
    ) -> None:
        """Record a tool error; increment AGENT_ERRORS (no audit emit -- D-01 scope)."""
        agent_type = kwargs.get("agent_type", "agent")
        try:
            AGENT_ERRORS.labels(agent_type=agent_type, outcome="error").inc()
        except Exception:
            pass

    def on_llm_error(
        self,
        error: BaseException,
        *,
        run_id: uuid.UUID,
        **kwargs: Any,
    ) -> None:
        """Record an LLM error; increment AGENT_ERRORS (no audit emit -- D-01 scope)."""
        agent_type = kwargs.get("agent_type", "agent")
        try:
            AGENT_ERRORS.labels(agent_type=agent_type, outcome="error").inc()
        except Exception:
            pass

    # -- Async variants (CRITICAL -- LangGraph astream dispatches ONLY these) --
    # Async variants build the payload via _build_llm_audit_payload then await both
    # AuditEventService.emit and CostLogService.emit directly so both rows are
    # guaranteed to be written on the running event loop.  They do NOT delegate to
    # the sync on_* variants -- the sync path cannot await either coroutine.

    async def aon_tool_start(
        self,
        serialized: dict[str, Any],
        input_str: str,
        *,
        run_id: uuid.UUID,
        **kwargs: Any,
    ) -> None:
        """Async tool_call_start -- builds payload and awaits audit emit directly."""
        tool_name = serialized.get("name", "unknown") if serialized else "unknown"
        pending = {
            "tool_name": tool_name,
            "input": str(input_str)[:500],
            "_t0": time.monotonic(),
        }
        self._pending_tool[str(run_id)] = pending
        agent_type = kwargs.get("agent_type", "agent")
        try:
            await self._svc.emit(
                AuditEventPayload(
                    tenant_id=self._tenant_id,
                    run_id=self._run_id,
                    event_type="tool_call_start",
                    agent_type=agent_type,
                    actor_id=f"system:{agent_type}",
                    payload={k: v for k, v in pending.items() if k != "_t0"},
                )
            )
        except Exception:
            pass

    async def aon_tool_end(
        self,
        output: str,
        *,
        run_id: uuid.UUID,
        **kwargs: Any,
    ) -> None:
        """Async tool_call_end -- builds payload and awaits audit emit directly."""
        pending = self._pending_tool.pop(str(run_id), {})
        agent_type = kwargs.get("agent_type", "agent")
        t0 = pending.get("_t0")
        if t0 is not None:
            try:
                AGENT_REQUEST_LATENCY.labels(agent_type=agent_type).observe(
                    time.monotonic() - t0
                )
            except Exception:
                pass
        audit_payload = {k: v for k, v in pending.items() if k != "_t0"}
        try:
            await self._svc.emit(
                AuditEventPayload(
                    tenant_id=self._tenant_id,
                    run_id=self._run_id,
                    event_type="tool_call_end",
                    agent_type=agent_type,
                    actor_id=f"system:{agent_type}",
                    payload={**audit_payload, "output_snippet": str(output)[:200]},
                )
            )
        except Exception:
            pass

    async def aon_llm_end(
        self,
        response: Any,
        *,
        run_id: uuid.UUID,
        **kwargs: Any,
    ) -> None:
        """Async model_call -- awaits audit emit then awaits cost emit.

        Called by LangGraph astream() (the primary execution path in production).
        Both AuditEventService.emit and CostLogService.emit are awaited directly
        so both rows are written on the running event loop without creating
        detached tasks.  _build_llm_audit_payload handles Prometheus metrics.
        """
        agent_type = kwargs.get("agent_type", "agent")
        audit_payload, cost_kwargs = self._build_llm_audit_payload(response, agent_type)
        try:
            await self._svc.emit(audit_payload)
        except Exception:
            pass
        try:
            await self._cost_service.emit(**cost_kwargs)
        except Exception:
            pass

    async def aon_tool_error(
        self,
        error: BaseException,
        *,
        run_id: uuid.UUID,
        **kwargs: Any,
    ) -> None:
        """Async tool error -- delegates to on_tool_error."""
        self.on_tool_error(error, run_id=run_id, **kwargs)

    async def aon_llm_error(
        self,
        error: BaseException,
        *,
        run_id: uuid.UUID,
        **kwargs: Any,
    ) -> None:
        """Async LLM error -- delegates to on_llm_error."""
        self.on_llm_error(error, run_id=run_id, **kwargs)
