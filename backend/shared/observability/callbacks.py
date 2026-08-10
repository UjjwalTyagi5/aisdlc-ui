"""build_agent_callbacks — the single place agent LLM invocations attach observers.

Replaces the ~10 duplicated `config = {..., "callbacks": [AuditCallbackHandler(...)]}`
blocks across the Temporal activities and standalone agent APIs. Always returns the
existing AuditCallbackHandler (audit_events + agent_call_logs cost + Prometheus — the
behavior is unchanged). When Langfuse is enabled it ALSO appends the Langfuse LangChain
CallbackHandler and returns a context manager that opens the per-agent span under a
deterministic trace.

Deterministic trace grouping (the crux — see plan "Architecture"):
  Temporal activities run in the WORKER process, the agent APIs in the FastAPI process.
  We cannot share a live trace object across processes, so the trace id is derived from
  the run_id (pipeline) or session_id (standalone) via Langfuse.create_trace_id(seed=...).
  Every activity of one run therefore lands in ONE trace; every turn of one chat session
  groups by session_id.

Usage:
    callbacks, trace_cm = build_agent_callbacks(
        run_id=str(input.run_id), tenant_id=input.tenant_id, agent_type="design",
    )
    cfg = {"configurable": {"thread_id": ...}, "recursion_limit": N, "callbacks": callbacks}
    with trace_cm:
        final_state = await graph.ainvoke(state, config=cfg)

When Langfuse is disabled, trace_cm is contextlib.nullcontext() and callbacks holds only
the AuditCallbackHandler — a zero-cost, zero-behavior-change path.
"""
from __future__ import annotations

import logging
from contextlib import contextmanager, nullcontext
from typing import Any, Optional

from shared.observability.client import get_langfuse_client

logger = logging.getLogger(__name__)


def _set_run_project(project_id: Optional[str]) -> None:
    """Best-effort: stash project id in the run-scoped contextvar for budget enforcement."""
    try:
        from shared.services.model_resolver import set_run_project  # noqa: PLC0415

        set_run_project(project_id)
    except Exception:  # pragma: no cover - never break callback construction
        logger.debug("set_run_project failed (swallowed)", exc_info=True)


def build_agent_callbacks(
    *,
    run_id: Optional[str] = None,
    tenant_id: Optional[str] = None,
    session_id: Optional[str] = None,
    user_id: Optional[str] = None,
    agent_type: str = "agent",
    model: Optional[str] = None,
    offering_id: Optional[str] = None,
    project_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
    audit_service: Any = None,
    cost_service: Any = None,
):
    """Return (callbacks_list, trace_context_manager).

    callbacks_list always contains an AuditCallbackHandler tied to run_id/tenant_id.
    trace_context_manager is a nullcontext when Langfuse is disabled, else a CM that
    opens the per-agent Langfuse span under the run/session-seeded trace and stamps
    trace-level attributes (session_id, user_id, tenant tag, model/offering metadata).
    """
    from shared.audit import AuditCallbackHandler  # noqa: PLC0415

    if audit_service is None:
        from shared.audit.service import audit_service as _svc  # noqa: PLC0415

        audit_service = _svc

    audit = AuditCallbackHandler(
        audit_service,
        run_id=str(run_id or session_id or ""),
        tenant_id=tenant_id or "",
        cost_service=cost_service,
    )
    # Usage meter (Redis TPM + monthly-cost counters) — always attached; it powers
    # tpm_limit / cost_limit_usd enforcement independently of Langfuse and of the
    # (unreliable) agent_call_logs path. No-op when Redis is unavailable.
    from shared.observability.usage_meter import UsageMeterCallbackHandler  # noqa: PLC0415

    meter = UsageMeterCallbackHandler(tenant_id or "", offering_id, project_id)
    callbacks: list = [audit, meter]

    # Thread the run's project into this async context so resolve_model_for_run can
    # enforce project/workspace budgets mid-run (org budget applies even when None).
    _set_run_project(project_id)

    client = get_langfuse_client()
    if client is None:
        return callbacks, nullcontext()

    try:
        from langfuse.langchain import CallbackHandler  # noqa: PLC0415

        callbacks.append(CallbackHandler())
    except Exception:  # pragma: no cover - defensive
        logger.debug("Langfuse CallbackHandler unavailable; audit-only", exc_info=True)
        return callbacks, nullcontext()

    seed = str(run_id or session_id or "")
    trace_id = client.create_trace_id(seed=seed) if seed else None

    tags = []
    if tenant_id:
        tags.append(f"tenant:{tenant_id}")
    if workspace_id:
        tags.append(f"workspace:{workspace_id}")  # org ⊇ workspace ⊇ project hierarchy
    if project_id:
        tags.append(f"project:{project_id}")  # enables project-scoped cost/token metrics
    metadata: dict[str, Any] = {"agent_type": agent_type}
    if workspace_id:
        metadata["workspace_id"] = str(workspace_id)
    if model:
        metadata["model"] = model
    if offering_id:
        metadata["offering_id"] = offering_id
    if project_id:
        metadata["project_id"] = str(project_id)

    @contextmanager
    def _trace_cm():
        trace_context = {"trace_id": trace_id} if trace_id else None
        try:
            with client.start_as_current_span(name=agent_type, trace_context=trace_context):
                try:
                    client.update_current_trace(
                        name=f"sdlc:{agent_type}",
                        session_id=session_id or run_id or None,
                        user_id=user_id or None,
                        tags=tags,
                        metadata=metadata,
                    )
                except Exception:  # pragma: no cover - defensive
                    logger.debug("update_current_trace failed (swallowed)", exc_info=True)
                yield
        except Exception:  # pragma: no cover - span open failure must not break the run
            logger.debug("Langfuse span open failed (swallowed)", exc_info=True)
            yield
        finally:
            # Flush after the per-agent span closes so the pipeline trace is durable
            # even in short-lived / idle Temporal worker processes (the SDK's timer
            # flush may not fire before the worker parks).
            try:
                client.flush()
            except Exception:  # pragma: no cover - defensive
                logger.debug("Langfuse flush failed (swallowed)", exc_info=True)

    return callbacks, _trace_cm()


def langfuse_langchain_extras(
    *,
    session_id: Optional[str] = None,
    run_id: Optional[str] = None,
    tenant_id: Optional[str] = None,
    user_id: Optional[str] = None,
    agent_type: str = "agent",
    model: Optional[str] = None,
    offering_id: Optional[str] = None,
    project_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
) -> tuple[list, dict]:
    """Return (extra_callbacks, config_metadata) for a standalone (session) call.

    Unlike build_agent_callbacks (which opens an explicit span for pipeline
    activities), this relies on the Langfuse LangChain handler's reserved-metadata
    keys (langfuse_session_id / langfuse_user_id / langfuse_tags) so the handler
    creates a session-grouped, tenant-tagged trace with NO context-manager wrapping
    — suited to the spread-out streaming loops in the agent API routers. Merge the
    returned lists/dicts into the existing LangGraph config:

        _lf_cbs, _lf_meta = langfuse_langchain_extras(session_id=..., tenant_id=...)
        config = {..., "callbacks": [audit, *_lf_cbs], "metadata": _lf_meta}

    Always includes the Redis usage meter (for tpm_limit / cost_limit_usd
    enforcement); the Langfuse handler is added on top when tracing is enabled.
    Returns ([meter], {}) when Langfuse is disabled.
    """
    from shared.observability.usage_meter import UsageMeterCallbackHandler  # noqa: PLC0415

    extra_cbs: list = [UsageMeterCallbackHandler(tenant_id or "", offering_id, project_id)]
    _set_run_project(project_id)

    client = get_langfuse_client()
    if client is None:
        return extra_cbs, {}
    try:
        from langfuse.langchain import CallbackHandler  # noqa: PLC0415
    except Exception:  # pragma: no cover - defensive
        return extra_cbs, {}

    _tags = []
    if tenant_id:
        _tags.append(f"tenant:{tenant_id}")
    if workspace_id:
        _tags.append(f"workspace:{workspace_id}")  # org ⊇ workspace ⊇ project hierarchy
    if project_id:
        _tags.append(f"project:{project_id}")  # enables project-scoped cost/token metrics
    meta: dict[str, Any] = {
        "langfuse_session_id": session_id or run_id or None,
        "langfuse_tags": _tags,
        "agent_type": agent_type,
    }
    if workspace_id:
        meta["workspace_id"] = str(workspace_id)
    if user_id:
        meta["langfuse_user_id"] = user_id
    if model:
        meta["model"] = model
    if offering_id:
        meta["offering_id"] = offering_id
    if project_id:
        meta["project_id"] = str(project_id)
    return [*extra_cbs, CallbackHandler()], meta
