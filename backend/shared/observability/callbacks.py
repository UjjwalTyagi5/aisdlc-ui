"""langfuse_langchain_extras — the single place agent LLM invocations attach observers.

Returns the callbacks and the LangGraph config metadata an agent turn needs: always the
Redis usage meter (which powers tpm_limit / cost_limit_usd enforcement independently of
Langfuse), plus the Langfuse LangChain CallbackHandler when tracing is enabled.

ONE EMISSION PATH, DELIBERATELY. There used to be a second factory,
`build_agent_callbacks`, written for pipeline stage runners that would open an explicit
span under a trace id derived from the run via `Langfuse.create_trace_id(seed=...)`.
Those stage runners were never built — `backend/workflows/activities/` has no such
module — so it sat at zero call sites while all fifteen live agent routes used this
function. It was removed rather than kept: an unreachable second path that appears to
guarantee cross-process trace grouping is worse than not having one, because the
guarantee reads as true. If pipeline-side tracing is wanted later, extend THIS function;
do not reintroduce a parallel one.

Grouping is therefore by session, via the Langfuse handler's reserved metadata keys
(`langfuse_session_id` / `langfuse_user_id` / `langfuse_tags`) rather than an explicit
span — which suits the spread-out streaming loops in the agent API routers, where there
is no single block to wrap in a context manager.

Scope travels as tags (`tenant:` / `workspace:` / `project:`), not columns: Langfuse has
no notion of this product's org > business unit > project hierarchy, so the read side
(shared/routers/traces.py) filters on those tags and on `userId`.

When Langfuse is disabled this returns ([meter], {}) — a zero-cost, zero-behavior-change
path, and every trace endpoint answers empty rather than erroring.
"""
from __future__ import annotations

import logging
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
    public_key: Optional[str] = None,
) -> tuple[list, dict]:
    """Return (extra_callbacks, config_metadata) for a standalone (session) call.

    Relies on the Langfuse LangChain handler's reserved-metadata keys
    (langfuse_session_id / langfuse_user_id / langfuse_tags) so the handler creates a
    session-grouped, tenant-tagged trace with NO context-manager wrapping — suited to
    the spread-out streaming loops in the agent API routers, which have no single block
    to wrap. Merge the returned lists/dicts into the existing LangGraph config:

        _lf_cbs, _lf_meta = langfuse_langchain_extras(session_id=..., tenant_id=...)
        config = {..., "callbacks": [audit, *_lf_cbs], "metadata": _lf_meta}

    Always includes the Redis usage meter (for tpm_limit / cost_limit_usd
    enforcement); the Langfuse handler is added on top when tracing is enabled.
    Returns ([meter], {}) when Langfuse is disabled.
    """
    from shared.observability.usage_meter import UsageMeterCallbackHandler  # noqa: PLC0415

    extra_cbs: list = [UsageMeterCallbackHandler(tenant_id or "", offering_id, project_id)]
    _set_run_project(project_id)

    # `public_key` selects WHICH Langfuse project this turn writes to. Each SDLC project
    # has its own, so the handler must be bound to that project's client rather than the
    # process-wide default — otherwise every project's traces land in one bucket again
    # and the isolation the binding table exists to provide is lost at the last step.
    if public_key is None and get_langfuse_client() is None:
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
    if run_id:
        # The read side has always looked for this — traces.py maps
        # metadata["run_id"] onto TraceListItem.runId — and nothing ever wrote it,
        # so every row's runId came back null and a trace could not be tied to the
        # run that produced it. FR-08 asks for traces "correlated to ... workstream";
        # this is the correlation.
        meta["run_id"] = str(run_id)
    handler = CallbackHandler(public_key=public_key) if public_key else CallbackHandler()
    return [*extra_cbs, handler], meta


async def agent_trace(
    *,
    session_id: Optional[str],
    agent_type: str,
    request: Any = None,
    claims: Optional[dict] = None,
    user_id: Optional[str] = None,
    tenant_id: Optional[str] = None,
    project_id: Optional[str] = None,
    run_id: Optional[str] = None,
    model: Optional[str] = None,
    offering_id: Optional[str] = None,
) -> tuple[list, dict]:
    """THE call for an agent route to attach observability. Returns (callbacks, metadata).

    WHY THIS EXISTS. `langfuse_langchain_extras` takes nine keyword arguments and was
    called directly from fifteen places. They drifted, and the drift was invisible
    because a missing argument is a default, not an error: only five sites passed
    `user_id`, three passed `workspace_id`, and two passed no `tenant_id` at all. The
    result was a Traces page that cannot answer "show me this person's runs" for
    two-thirds of agent traffic, and cannot group by business unit for almost any of
    it — a reporting gap with no failing test and no error in a log.

    So identity is resolved HERE, once, from whatever the route has:

      REST routes pass `request`   -> request.state.user_id / tenant_id
      WS routes   pass `claims`    -> the redeemed ticket's user_id / tenant_id
      anything else may pass user_id / tenant_id explicitly

    IDENTITY IS NEVER TAKEN FROM A REQUEST BODY. Several agent routes still accept a
    `user_id` form field for wire compatibility and explicitly do not trust it (see
    requirements_agent_api's "NOT trusted for identity"); this function only reads the
    authenticated token or ticket, so a caller cannot label their traces as someone else.

    `workspace_id` — the business unit, and the tag the unit-scoped Traces view filters
    on — is resolved from the project rather than passed in. It is a cached lookup that
    already fails soft to None, and asking fifteen call sites to remember it is how it
    came to be missing from twelve of them.
    """
    _tenant = (
        tenant_id
        or (getattr(getattr(request, "state", None), "tenant_id", None) if request else None)
        or (claims or {}).get("tenant_id")
        or ""
    )
    _user = (
        user_id
        or (getattr(getattr(request, "state", None), "user_id", None) if request else None)
        or (claims or {}).get("user_id")
        or ""
    )

    workspace_id = None
    if project_id:
        try:
            from shared.services.budget_store import workspace_id_for_project  # noqa: PLC0415

            workspace_id = await workspace_id_for_project(str(_tenant), str(project_id))
        except Exception:  # pragma: no cover - tags are best-effort, never fatal
            logger.debug("workspace resolution failed (swallowed)", exc_info=True)

    # WHICH LANGFUSE PROJECT THIS TURN WRITES TO. Each SDLC project owns one, so the
    # binding decides the destination; without it every project would fall back to the
    # single shared client and the isolation would exist in the database and nowhere
    # else. Best-effort by design: an unreachable Langfuse leaves `public_key` None, the
    # handler falls back to the default client if one is configured, and if neither
    # exists the turn simply runs untraced rather than failing.
    public_key = None
    if project_id and _tenant:
        try:
            from shared.db import get_db_session_for_tenant  # noqa: PLC0415
            from shared.observability.bindings import (  # noqa: PLC0415
                client_for_binding,
                ensure_binding,
            )

            async with get_db_session_for_tenant(str(_tenant)) as _s:
                binding = await ensure_binding(
                    _s, tenant_id=str(_tenant), project_id=str(project_id)
                )
            if binding is not None:
                # Constructing the client REGISTERS it with the SDK under its public
                # key, which is how CallbackHandler(public_key=…) finds it below.
                _client = client_for_binding(binding)
                if _client is not None:
                    public_key = binding.public_key
                    # Also bind it to this run's context, for agents that do NOT go
                    # through LangChain — the monitoring agent calls litellm directly,
                    # so it has no handler to carry the destination and would otherwise
                    # be untraceable without a global client.
                    from shared.observability.bindings import (  # noqa: PLC0415
                        set_current_client,
                    )

                    set_current_client(_client)
        except Exception:  # pragma: no cover - never fail a run over observability
            logger.debug("langfuse binding resolution failed (swallowed)", exc_info=True)

    return langfuse_langchain_extras(
        session_id=session_id,
        run_id=run_id,
        tenant_id=str(_tenant),
        user_id=str(_user),
        agent_type=agent_type,
        model=model,
        offering_id=offering_id,
        project_id=project_id,
        workspace_id=workspace_id,
        public_key=public_key,
    )
