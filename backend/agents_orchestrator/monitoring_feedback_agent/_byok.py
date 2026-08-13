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
