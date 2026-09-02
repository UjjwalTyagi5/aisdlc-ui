"""Phase 3 — BYOK runtime model resolver. Maps a tenant (+ optional requested
model) to a ResolvedModel: which provider, which model, the org's BYOK key, and
a non-secret alias. Fails CLOSED (NoModelConfiguredError) when the org has no
valid+enabled model — there is NO platform fallback (spec D-4).

Security: the resolved api_key is NEVER logged. Only `alias` is safe to log.
"""
from __future__ import annotations

import contextvars
import logging
import time
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy import text

from shared.db import get_db_session_for_tenant
from shared.services import secret_store

logger = logging.getLogger(__name__)

# Catalog provider name -> LiteLLM custom_llm_provider token.
_LITELLM_PROVIDER = {"anthropic": "anthropic", "openai": "openai", "google": "gemini"}

# In-process BYOK-key cache keyed by (tenant_id, secret_ref) -> (api_key, expires_at).
# The secret_store read is the one network hop in resolution; on a slow/flaky link
# (e.g. Key Vault over a phone hotspot) it can intermittently time out and surface as a
# spurious "no model configured". Caching the key for a short TTL means only the FIRST
# resolve per key pays that cost; every later agent turn reuses it with no KV read.
# The plaintext key lives only in-process and is never logged (same contract as elsewhere).
_KEY_CACHE: dict[tuple[str, str], tuple[str, float]] = {}
_KEY_CACHE_TTL_SECONDS = 300


async def _resolve_secret_cached(tenant_id: str, secret_ref: str) -> Optional[str]:
    """Return the BYOK key for (tenant, secret_ref), using the short-lived in-process
    cache. On a cache miss (or expiry) read through secret_store and cache a non-empty
    result. A miss/None is NOT cached, so a genuinely-absent key keeps failing closed."""
    key = (tenant_id, secret_ref)
    hit = _KEY_CACHE.get(key)
    if hit is not None and hit[1] > time.monotonic():
        return hit[0]
    value = await secret_store.get_secret(tenant_id, secret_ref)
    if value:
        _KEY_CACHE[key] = (value, time.monotonic() + _KEY_CACHE_TTL_SECONDS)
    return value


@dataclass
class ResolvedModel:
    provider: str            # catalog provider: anthropic | openai | google
    litellm_provider: str    # LiteLLM token: anthropic | openai | gemini
    model: str               # catalog model id, e.g. claude-sonnet-4-6
    api_key: str             # BYOK key — never log this
    base_url: Optional[str]  # None for standard providers (v1)
    alias: str               # non-secret label for cost logs / metadata
    offering_id: str = ""    # the exact offering (provider connection + model) chosen
    display_name: str = ""   # provider connection name — safe to log / show in UI
    rpm_limit: Optional[int] = None    # per-model requests/min cap (enforced at resolve)
    tpm_limit: Optional[int] = None    # per-model tokens/min cap (enforced at resolve)
    cost_limit_usd: Optional[float] = None  # per-model monthly USD budget (enforced at resolve)
    max_cost_per_call_usd: Optional[float] = None  # org-level per-call cap (provider row)
    input_price_per_million: Optional[float] = None  # USD/1M input tokens (offering pricing)
    extra_kwargs: dict = field(default_factory=dict)  # no-training/provider-specific call params


class NoModelConfiguredError(Exception):
    """The tenant has no valid+enabled model — the run must fail closed."""


class ModelNotEnabledError(Exception):
    """A requested model is not in the tenant's enabled+valid set."""


_RESOLVED_MODEL: "contextvars.ContextVar[ResolvedModel | None]" = contextvars.ContextVar(
    "resolved_model", default=None)


def set_resolved_model(resolved: "ResolvedModel | None") -> None:
    """Stash the run's resolved model so tool-level generation helpers can reuse it
    without re-resolving (and without an async DB call from an executor thread)."""
    _RESOLVED_MODEL.set(resolved)


def get_resolved_model() -> "ResolvedModel | None":
    """Return the resolved model set for the current run context, or None."""
    return _RESOLVED_MODEL.get()


# Run-scoped project id — set once at an activity/agent entry so resolve_model_for_run
# can enforce project/workspace budgets mid-run without every caller threading it
# explicitly (mirrors the set_resolved_model contextvar pattern).
_RUN_PROJECT: "contextvars.ContextVar[str | None]" = contextvars.ContextVar(
    "run_project_id", default=None)


def set_run_project(project_id: "str | None") -> None:
    """Stash the current run's project id for budget enforcement in this async context."""
    _RUN_PROJECT.set(str(project_id) if project_id else None)


def get_run_project() -> "str | None":
    """Return the run-scoped project id for the current context, or None."""
    return _RUN_PROJECT.get()


# tenant_id -> (ResolvedModel-by-model dict, fetched_at_monotonic). 5-min TTL
# so per-turn agent calls avoid DB + secret_store round-trips.
_CACHE: dict[str, tuple[dict, float]] = {}
_CACHE_TTL_SECONDS = 300


def _alias(tenant_id: str, provider_id: str) -> str:
    return f"tenant:{tenant_id}:{provider_id}"


async def _load_enabled(tenant_id: str) -> list[dict]:
    """Rows of valid+enabled offerings with the data needed to build a key fetch.
    Explicit p.tenant_id filter is defence-in-depth under superuser dev connections."""
    async with get_db_session_for_tenant(tenant_id) as s:
        rows = (await s.execute(text(
            "SELECT o.id AS offering_id, o.model_id, o.is_default, "
            "o.rpm_limit, o.tpm_limit, o.cost_limit_usd, o.input_price_per_million, "
            "p.id AS provider_id, p.provider, p.display_name, p.secret_ref, p.api_base, "
            "p.max_cost_per_call_usd "
            "FROM model_offerings o JOIN model_providers p ON p.id = o.provider_id "
            "WHERE o.enabled = true AND p.status = 'valid' AND p.tenant_id = :t AND o.tenant_id = :t "
            "ORDER BY p.display_name, p.provider, o.model_id"
        ), {"t": tenant_id})).fetchall()
    return [
        {"offering_id": str(r.offering_id), "model_id": r.model_id, "is_default": bool(r.is_default),
         "provider_id": str(r.provider_id), "provider": r.provider,
         "display_name": r.display_name, "secret_ref": r.secret_ref, "api_base": r.api_base,
         "rpm_limit": r.rpm_limit, "tpm_limit": r.tpm_limit,
         "cost_limit_usd": float(r.cost_limit_usd) if r.cost_limit_usd is not None else None,
         "max_cost_per_call_usd": float(r.max_cost_per_call_usd) if r.max_cost_per_call_usd is not None else None,
         "input_price_per_million": float(r.input_price_per_million) if r.input_price_per_million is not None else None}
        for r in rows
    ]


async def resolve_model_for_run(
    tenant_id: str,
    requested_model_id: Optional[str] = None,
    *,
    offering_id: Optional[str] = None,
    project_id: Optional[str] = None,
) -> ResolvedModel:
    """Resolve the model+key a run should use.

    Precedence (most specific first):
      - offering_id given -> the EXACT provider connection + model (unambiguous even
        when two connections expose the same model_id); else ModelNotEnabledError.
      - requested_model_id given -> first enabled offering with that model_id
        (legacy/back-compat path; ambiguous if duplicated — prefer offering_id).
      - neither -> the org default offering (is_default), or the single offering.
    No valid+enabled offering -> NoModelConfiguredError (fail closed, no fallback).
    """
    if not tenant_id:
        raise NoModelConfiguredError("no tenant context for model resolution")

    offerings = await _load_enabled(tenant_id)
    if not offerings:
        raise NoModelConfiguredError(
            f"tenant {tenant_id} has no valid, enabled model provider configured")

    from shared.services.model_grants import effective_project_offerings  # noqa: PLC0415

    effective_ids = await effective_project_offerings(
        tenant_id, project_id or _RUN_PROJECT.get()
    )
    if effective_ids is not None:
        offerings = [o for o in offerings if o["offering_id"] in effective_ids]
        if not offerings:
            raise NoModelConfiguredError(
                f"tenant {tenant_id} has grants configured but none apply to this project"
            )

    if offering_id:
        chosen = next((o for o in offerings if o["offering_id"] == offering_id), None)
        if chosen is None:
            raise ModelNotEnabledError(
                f"model offering {offering_id!r} is not enabled for this org")
    elif requested_model_id:
        chosen = next((o for o in offerings if o["model_id"] == requested_model_id), None)
        if chosen is None:
            raise ModelNotEnabledError(
                f"model {requested_model_id!r} is not enabled for this org")
    else:
        chosen = next((o for o in offerings if o["is_default"]), offerings[0])

    # Live per-model limit enforcement (org/workspace-scoped via the offering).
    # RPM counts resolutions this minute; TPM/cost read the Redis usage meter fed by
    # UsageMeterCallbackHandler. Each raises a typed *LimitError over the cap and fails
    # open if Redis is unavailable. Checked BEFORE fetching the secret so an over-limit
    # run does no extra work.
    from shared.services.model_rate_limit import (  # noqa: PLC0415
        enforce_cost,
        enforce_rpm,
        enforce_tpm,
    )

    _off = chosen["offering_id"]
    await enforce_rpm(tenant_id, _off, chosen.get("rpm_limit"))
    await enforce_tpm(tenant_id, _off, chosen.get("tpm_limit"))
    await enforce_cost(tenant_id, _off, chosen.get("cost_limit_usd"))

    # Hierarchical monthly budget gate (org ⊇ workspace ⊇ project). Fails the run
    # closed mid-pipeline once any scope's calendar-month spend hits its budget.
    # project_id falls back to the run-scoped contextvar so pipeline activities that
    # set it once at entry get project/workspace enforcement without threading it here.
    from shared.services.budget_guard import check_budgets  # noqa: PLC0415

    await check_budgets(tenant_id, project_id or _RUN_PROJECT.get())

    api_key = await _resolve_secret_cached(tenant_id, chosen["secret_ref"])
    if not api_key:
        # A 'valid' provider with a missing secret is a configuration error, not a fallback.
        raise NoModelConfiguredError(
            f"BYOK key missing for provider {chosen['provider_id']} (tenant {tenant_id})")

    from shared.services.model_call_wrapper import no_training_kwargs  # noqa: PLC0415

    resolved = ResolvedModel(
        provider=chosen["provider"],
        litellm_provider=_LITELLM_PROVIDER.get(chosen["provider"], chosen["provider"]),
        model=chosen["model_id"],
        api_key=api_key,
        # The provider's custom endpoint (self-hosted / OpenAI-compatible gateway).
        # Previously hard-coded None, so a custom provider VERIFIED against its own
        # api_base and then sent live traffic to the vendor default instead.
        base_url=chosen.get("api_base") or None,
        alias=_alias(tenant_id, chosen["provider_id"]),
        offering_id=chosen["offering_id"],
        display_name=chosen["display_name"],
        rpm_limit=chosen.get("rpm_limit"),
        tpm_limit=chosen.get("tpm_limit"),
        cost_limit_usd=chosen.get("cost_limit_usd"),
        max_cost_per_call_usd=chosen.get("max_cost_per_call_usd"),
        input_price_per_million=chosen.get("input_price_per_million"),
        extra_kwargs=no_training_kwargs(chosen["provider"]),
    )
    logger.debug("model resolved tenant=%s model=%s offering=%s alias=%s",
                 tenant_id, resolved.model, resolved.offering_id, resolved.alias)
    return resolved


def invalidate_key_cache(tenant_id: str, secret_ref: str | None = None) -> None:
    """Drop cached BYOK key(s) so a rotated or revoked key stops working immediately.

    Without this the 300s TTL above keeps a REVOKED key usable in-process for up to
    five minutes after an admin rotates it — which defeats the point of rotating a
    compromised credential. Call on every rotate and every delete.
    """
    if secret_ref is not None:
        _KEY_CACHE.pop((tenant_id, secret_ref), None)
        return
    for key in [k for k in _KEY_CACHE if k[0] == tenant_id]:
        _KEY_CACHE.pop(key, None)


def temperature_kwargs(model: str, temperature: float) -> dict:
    """`{"temperature": ...}`, or `{}` for a model that structurally cannot take it.

    gpt-5-family models (including gpt-5-codex) accept only the default temperature of
    1, and litellm raises `UnsupportedParamsError` BEFORE the call rather than clamping
    the value — so a tenant on azure/gpt-5-mini got a failed turn every time, with only
    the exception type in the log (never `str(exc)`, which routinely echoes credentials
    back). The Development agent hit this live on 2026-08-31 and fixed it in
    `dev_agent._build_llm`; Requirements and Design had the same hardcoded temperature
    in four more places, two `ChatLiteLLM` builds and two direct `litellm` calls.

    A HELPER RATHER THAN A FIFTH COPY, and deliberately NOT `litellm.drop_params=True`.
    Dropping params globally would silently swallow every other unsupported parameter
    for every model everywhere, turning a loud, fixable error into a quiet behaviour
    change. This is the narrow, verified exception for the one family that cannot take
    the parameter; every other model keeps the low, determinism-favouring temperature
    its agent chose.
    """
    return {} if "gpt-5" in (model or "").lower() else {"temperature": temperature}


def resolve_chat_model(
    *,
    model_id: str | None = None,
    offering_id: str | None = None,
    tools: list | None = None,
    system_prompt: str | None = None,  # noqa: ARG001 — caller owns prompt assembly
):
    """Build a tool-bound chat model from the run's BYOK-resolved model.

    Reads the ResolvedModel the run's primary node stashed via set_resolved_model().
    Mirrors testing_agent/config/shared.py:build_llm, which is the canonical shape.

    Fails CLOSED under AGENT_RUNTIME_MODE == "enterprise": there is no platform-key
    fallback (spec D-4). Local dev falls back to ANTHROPIC_API_KEY, same as every
    other agent.

    `system_prompt` is accepted for call-site compatibility but deliberately NOT
    injected — each agent_node already prepends its own SystemMessage, and applying
    it here too would duplicate the prompt.

    `model_id` / `offering_id` are advisory: resolution for the run already happened
    up front (that is what enforces budgets, grants and rate limits). They are used
    only to detect a node asking for a DIFFERENT model than the run resolved, which
    is a caller bug worth surfacing in logs.
    """
    resolved = get_resolved_model()
    tools = tools or []

    if resolved is not None:
        if model_id and resolved.model != model_id:
            logger.debug("node requested model=%s but run resolved %s — using the run's",
                         model_id, resolved.model)
        if offering_id and resolved.offering_id and resolved.offering_id != offering_id:
            logger.debug("node requested offering=%s but run resolved %s — using the run's",
                         offering_id, resolved.offering_id)
        from langchain_litellm import ChatLiteLLM  # noqa: PLC0415 — importing litellm costs ~7s
        llm = ChatLiteLLM(
            model=resolved.model,
            custom_llm_provider=resolved.litellm_provider,
            api_base=resolved.base_url or None,
            api_key=resolved.api_key,
            max_retries=2,
            max_tokens=8192,
            **resolved.extra_kwargs,
        )
        return llm.bind_tools(tools) if tools else llm

    # No model resolved for this run. The run resolves + stashes up front, but a node
    # re-entering asyncio in an executor thread can drop the contextvar, so this path
    # is reachable legitimately in local dev.
    from config.env import AGENT_RUNTIME_MODE, ANTHROPIC_API_KEY, ANTHROPIC_MODEL  # noqa: PLC0415

    if AGENT_RUNTIME_MODE == "enterprise" or not ANTHROPIC_API_KEY:
        raise NoModelConfiguredError(
            "No BYOK model resolved for this run. An administrator must configure and "
            "verify a model provider in Org Settings -> Model Providers."
        )
    from langchain_litellm import ChatLiteLLM  # noqa: PLC0415
    llm = ChatLiteLLM(
        model=ANTHROPIC_MODEL,
        custom_llm_provider="anthropic",
        api_key=ANTHROPIC_API_KEY,
        max_retries=2,
        max_tokens=8192,
    )
    return llm.bind_tools(tools) if tools else llm
