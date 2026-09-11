"""BYOK helper for the monitoring/feedback agent (P3.6 Task B4).

This agent is a PROTOTYPE: its router is not mounted in process_api.py and it has
no live tenant-aware caller (the pipeline sequences only
requirements -> design -> development -> testing; there is no monitoring phase or
activity). The previous LLM sites built calls with the shared *platform*
LITELLM_API_KEY, which BYOK D-4 forbids.

Until a tenant-aware caller is wired in (one that calls
`resolve_model_for_run(tenant_id, model_id)` and `set_resolved_model(...)` at the
start of the request), these LLM calls FAIL CLOSED: if no ResolvedModel is present
on the run contextvar, `resolved_litellm_kwargs()` raises NoModelConfiguredError —
there is NO platform-key fallback.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

from shared.services.model_resolver import (
    get_resolved_model,
    NoModelConfiguredError,
)


def resolved_litellm_kwargs() -> dict:
    """Return litellm.completion/acompletion kwargs (model, api_key, api_base,
    custom_llm_provider) for the run's BYOK-resolved model.

    Fails closed: raises NoModelConfiguredError when no model has been resolved
    onto the contextvar for the current run — never falls back to a platform key.
    """
    resolved = get_resolved_model()
    if resolved is None:
        raise NoModelConfiguredError(
            "monitoring agent has no resolved BYOK model for this run; a "
            "tenant-aware caller must resolve_model_for_run + set_resolved_model "
            "before invoking LLM analysis (no platform-key fallback — BYOK D-4)"
        )
    return {
        "model": resolved.model,
        "custom_llm_provider": resolved.litellm_provider,
        "api_key": resolved.api_key,
        "api_base": resolved.base_url,
    }


def traced_completion(*, name: str, **kwargs):
    """`litellm.completion` with a Langfuse generation around it, when tracing is on.

    WHY THIS AGENT NEEDS ITS OWN WRAPPER. Every other agent reaches the model through
    LangChain, so the Langfuse CallbackHandler observes the call and records the model,
    tokens and cost for free. This one calls litellm directly, which no handler sees —
    which is why it was the last agent producing no traces at all, failing FR-08's
    "traces for agent/tool/model execution" while looking perfectly healthy.

    The client comes from the run's context rather than a module global, so the
    generation lands in THIS project's Langfuse project and not whichever one a global
    happened to hold. No client bound (tracing off, or an unbound project) means the
    call runs exactly as before.
    """
    import litellm  # noqa: PLC0415 — deferred: importing litellm costs ~7s

    try:
        from shared.observability.bindings import get_current_client  # noqa: PLC0415

        client = get_current_client()
    except Exception:
        client = None

    if client is None:
        return litellm.completion(**kwargs)

    try:
        with client.start_as_current_generation(
            name=name,
            model=kwargs.get("model"),
            input=kwargs.get("messages"),
        ) as gen:
            response = litellm.completion(**kwargs)
            try:
                usage = getattr(response, "usage", None)
                gen.update(
                    output=response.choices[0].message.content,
                    # litellm normalises provider usage onto these names, so cost and
                    # token attribution work the same as the LangChain path.
                    usage_details={
                        "input": getattr(usage, "prompt_tokens", None),
                        "output": getattr(usage, "completion_tokens", None),
                        "total": getattr(usage, "total_tokens", None),
                    } if usage else None,
                )
            except Exception:  # pragma: no cover - recording must never break the call
                pass
            return response
    except Exception:
        # Tracing is not worth an agent turn. Fall back to the untraced call rather
        # than letting an observability failure surface as an analysis failure.
        logger.warning("monitoring agent: traced completion failed, retrying untraced",
                       exc_info=True)
        return litellm.completion(**kwargs)
