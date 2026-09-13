"""Tracing for LLM calls that do NOT go through LangChain.

WHY THIS IS NEEDED AT ALL. Nearly all observability on this platform rides on
LangChain callbacks: `agent_trace` returns a Langfuse `CallbackHandler` and a
`UsageMeterCallbackHandler`, and LangChain invokes both around every model call.
An agent that calls `litellm.completion(...)` directly is invisible to all of it —
no trace, no token count, no cost, no `usage_monthly` row — while looking
completely healthy, because the call itself succeeds and the agent answers.

That is not a hypothetical. On 2026-09-13 a chat turn with the Requirements agent
produced real replies and left `agent_call_logs`, `usage_monthly` and all three
Langfuse projects empty. The binding resolved, the client authenticated
(`auth_check: True`), the handler was constructed — and nothing ever called it,
because that code path is `litellm.completion(...)` with no `callbacks=`.

The monitoring agent had already solved this for itself in
`monitoring_feedback_agent/_byok.py`, whose docstring said it was "the last agent
producing no traces at all". It was not; it was the only one that had noticed.
This is that wrapper, moved somewhere every direct caller can reach.

THE CLIENT COMES FROM THE RUN'S CONTEXT, not a module global, so the generation
lands in THIS project's Langfuse project rather than whichever one a global
happened to hold. `agent_trace` binds it via `set_current_client`. No client bound
(tracing off, or an unbound project) means the call runs exactly as it did before.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def _current_client() -> Any | None:
    try:
        from shared.observability.bindings import get_current_client  # noqa: PLC0415

        return get_current_client()
    except Exception:  # pragma: no cover - observability must never break a call
        return None


def traced_completion(*, name: str, **kwargs):
    """`litellm.completion` wrapped in a Langfuse generation, when tracing is on.

    `name` is what appears on the Traces page, so it should say which step this is
    ("requirements:brief", not "completion").
    """
    import litellm  # noqa: PLC0415 — deferred: importing litellm costs ~7s

    client = _current_client()
    if client is None:
        return litellm.completion(**kwargs)

    try:
        with client.start_as_current_generation(
            name=name,
            model=kwargs.get("model"),
            input=kwargs.get("messages"),
        ) as gen:
            response = litellm.completion(**kwargs)
            _record(gen, response)
            return response
    except Exception:
        # Tracing is not worth an agent turn. Fall back to the untraced call rather
        # than letting an observability failure surface as an agent failure.
        logger.warning("traced completion failed, retrying untraced", exc_info=True)
        return litellm.completion(**kwargs)


async def traced_acompletion(*, name: str, **kwargs):
    """The async twin. Same contract, same fallback."""
    import litellm  # noqa: PLC0415

    client = _current_client()
    if client is None:
        return await litellm.acompletion(**kwargs)

    try:
        with client.start_as_current_generation(
            name=name,
            model=kwargs.get("model"),
            input=kwargs.get("messages"),
        ) as gen:
            response = await litellm.acompletion(**kwargs)
            _record(gen, response)
            return response
    except Exception:
        logger.warning("traced acompletion failed, retrying untraced", exc_info=True)
        return await litellm.acompletion(**kwargs)


def _record(gen: Any, response: Any) -> None:
    """Attach output and usage to the generation. Never raises."""
    try:
        usage = getattr(response, "usage", None)
        gen.update(
            output=response.choices[0].message.content,
            # litellm normalises provider usage onto these names, so cost and token
            # attribution work the same as they do on the LangChain path.
            usage_details={
                "input": getattr(usage, "prompt_tokens", None),
                "output": getattr(usage, "completion_tokens", None),
                "total": getattr(usage, "total_tokens", None),
            }
            if usage
            else None,
        )
    except Exception:  # pragma: no cover - recording must never break the call
        pass
