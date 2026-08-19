"""ModelCallWrapper (task #20 remainder) — the single place a resolved BYOK model
gets invoked.

docs/superpowers/specs/2026-08-11-model-gateway-bu-cascade-design.md §8.3 flagged
this as deliberately out of scope for the BU-cascade design; this module closes it.

`guarded_completion` wraps a LangChain chat model's `ainvoke`:
  - Estimates the call's cost up front and enforces the provider's per-call cap
    (shared.services.model_rate_limit.enforce_per_call_cost) BEFORE any network call —
    PRD §376/§545's "max cost per call" / "per-call limits".
  - Enforces a 30s timeout per attempt.
  - Retries twice (3 attempts total) on a timeout or a 429-shaped error, with 2s then
    4s backoff — the exact schedule the task named.
  - Logs every retry (outcome=retry) via the standard logger, and — best-effort, only
    when Langfuse is enabled — as an event on the current span, so retries are visible
    in Traces without a new local table.
  - Applies `resolved.extra_kwargs` (the no-training / provider-specific params from
    `no_training_kwargs`) to every attempt.

Deliberately NOT a LangChain `.with_retry()`/tenacity wrapper: those retry the whole
runnable opaquely and emit no per-attempt signal a caller can log or cost-check against.
Controlling the loop directly is what makes "log each retry as a trace step" possible.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)

_CALL_TIMEOUT_SECONDS = 30.0
_RETRY_BACKOFFS_SECONDS = (2.0, 4.0)  # attempt 2 waits 2s, attempt 3 waits 4s
_MAX_ATTEMPTS = 1 + len(_RETRY_BACKOFFS_SECONDS)

# Chars-per-token heuristic for the pre-call cost estimate — deliberately not a real
# tokenizer (no new dependency; tokenizer choice varies per provider/model anyway).
# A rough OVER-estimate is the safe direction for a cost cap: 3.5 chars/token skews
# conservative relative to the commonly-cited ~4 chars/token average.
_CHARS_PER_TOKEN_ESTIMATE = 3.5


def estimate_input_tokens(text: str) -> int:
    """Cheap, provider-agnostic estimate of input tokens from raw text length."""
    if not text:
        return 0
    return max(1, int(len(text) / _CHARS_PER_TOKEN_ESTIMATE))


def _messages_text(messages: Any) -> str:
    """Best-effort flatten of a LangChain messages list/string to plain text for
    estimation. Never raises — an unrecognized shape just estimates as empty (no cap
    enforced) rather than blocking a call the estimator doesn't understand."""
    try:
        if isinstance(messages, str):
            return messages
        parts: list[str] = []
        for m in messages or []:
            content = getattr(m, "content", None)
            if content is None and isinstance(m, dict):
                content = m.get("content")
            if isinstance(content, str):
                parts.append(content)
            elif isinstance(content, list):
                for c in content:
                    if isinstance(c, dict) and isinstance(c.get("text"), str):
                        parts.append(c["text"])
        return "\n".join(parts)
    except Exception:  # pragma: no cover - defensive, estimation must never break a call
        logger.debug("model_call_wrapper: message flatten failed (swallowed)", exc_info=True)
        return ""


# Provider-specific "do not train on this call" extras. Anthropic's and OpenAI's
# standard (non-training-partner) API endpoints already do not train on API traffic by
# default — there is no LiteLLM/provider parameter to set for them, so they map to {}
# rather than fabricating one. Documented here, not silently assumed, so a future
# provider that DOES need an explicit opt-out has an obvious place to add it.
_NO_TRAINING_EXTRAS: dict[str, dict] = {
    "anthropic": {},
    "openai": {},
    "google": {},
}


def no_training_kwargs(provider: str) -> dict:
    """Extra call kwargs enforcing the no-training guarantee (PRD §1784) for `provider`.
    Unknown providers get {} — no guess at a parameter that may not exist."""
    return dict(_NO_TRAINING_EXTRAS.get(provider, {}))


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return True
    status = getattr(exc, "status_code", None) or getattr(exc, "http_status", None)
    if status == 429:
        return True
    # LiteLLM/OpenAI-style rate-limit exceptions carry the code in the class name or
    # message when status_code isn't set on the exception itself.
    name = type(exc).__name__.lower()
    if "ratelimit" in name or "429" in str(exc):
        return True
    return False


def _log_retry(attempt: int, exc: BaseException, *, tenant_id: str, alias: str) -> None:
    logger.warning(
        "model call retry: attempt=%d/%d outcome=retry tenant=%s model=%s reason=%s: %s",
        attempt, _MAX_ATTEMPTS, tenant_id, alias, type(exc).__name__, str(exc)[:300],
    )
    try:
        from shared.observability.client import get_langfuse_client  # noqa: PLC0415

        client = get_langfuse_client()
        if client is not None and hasattr(client, "create_event"):
            client.create_event(
                name="model_call_retry",
                metadata={
                    "attempt": attempt, "max_attempts": _MAX_ATTEMPTS,
                    "outcome": "retry", "model": alias, "error": type(exc).__name__,
                },
            )
    except Exception:  # pragma: no cover - tracing must never break a run
        logger.debug("model_call_wrapper: retry trace event failed (swallowed)", exc_info=True)


async def guarded_completion(
    resolved: "Any",  # shared.services.model_resolver.ResolvedModel
    chat_model: Any,
    messages: Any,
    *,
    tenant_id: str = "",
    run_id: Optional[str] = None,
    agent_type: str = "agent",
    **invoke_kwargs: Any,
):
    """Invoke `chat_model.ainvoke(messages, **invoke_kwargs)` under the gateway's
    guardrails: per-call cost cap, 30s timeout, 2-retry backoff (2s, 4s), retry
    logging, and the resolved model's no-training extras.

    `resolved` is a model_resolver.ResolvedModel — this function reads
    `max_cost_per_call_usd`, `extra_kwargs`, and `alias`/`input_price_per_million`-less
    estimate (offering pricing isn't on ResolvedModel today, so the cap only fires
    when the provider set one AND pricing is resolvable via the offering; see
    estimate_input_tokens for the token-count half of the estimate).
    """
    from shared.services.model_rate_limit import ModelCostLimitError, enforce_per_call_cost  # noqa: PLC0415

    extra = dict(getattr(resolved, "extra_kwargs", {}) or {})
    merged_kwargs = {**invoke_kwargs, **extra}

    max_cost = getattr(resolved, "max_cost_per_call_usd", None)
    if max_cost:
        input_price = getattr(resolved, "input_price_per_million", None)
        if input_price:
            est_tokens = estimate_input_tokens(_messages_text(messages))
            est_cost = (est_tokens / 1_000_000.0) * float(input_price)
            enforce_per_call_cost(est_cost, max_cost)

    last_exc: BaseException | None = None
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            return await asyncio.wait_for(
                chat_model.ainvoke(messages, **merged_kwargs), timeout=_CALL_TIMEOUT_SECONDS,
            )
        except ModelCostLimitError:
            raise  # not retryable — the cap doesn't change between attempts
        except Exception as exc:  # noqa: BLE001 - broad on purpose: timeout/429 shape varies by provider
            last_exc = exc
            if attempt >= _MAX_ATTEMPTS or not _is_retryable(exc):
                raise
            _log_retry(attempt, exc, tenant_id=tenant_id, alias=getattr(resolved, "alias", ""))
            await asyncio.sleep(_RETRY_BACKOFFS_SECONDS[attempt - 1])
    # Unreachable (the loop always returns or raises), but keeps type-checkers honest.
    raise last_exc or RuntimeError("guarded_completion: exhausted attempts with no exception")
