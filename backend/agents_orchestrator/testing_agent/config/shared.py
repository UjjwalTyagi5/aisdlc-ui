"""Shared module-level singletons + broadcast helpers for the testing agent.

Phase 4a — extracted from `super_agent.py` lines 32-90 with no behavioural change.
Lazy-init the LLM so import-time failures (missing API key in test envs) don't
crash the FastAPI startup.
"""
from __future__ import annotations

import logging
import os
from typing import Optional


from config import sdlcSettings
from config.env import AGENTIC_BASE_URL, ANTHROPIC_API_KEY, ANTHROPIC_MODEL


# --- Logger (shared by every Node module) -----------------------------------
logger = logging.getLogger("testing_agent")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

esett = sdlcSettings()


# --- Broadcast helpers (with import-safe fallback) --------------------------
try:
    from config.ws_helper import set_session_id, broadcast_log, get_user_id, get_session_id
    from config.connection_manager import manager
    BROADCASTING_AVAILABLE = True
except ImportError:
    BROADCASTING_AVAILABLE = False
    manager = None  # type: ignore[assignment]

    def broadcast_log(_manager, message, level="INFO"):  # type: ignore[no-redef]
        print(f"[{level}] {message}")

    def get_user_id():  # type: ignore[no-redef]
        return "default_user"

    def get_session_id():  # type: ignore[no-redef]
        return "default_session"

    def set_session_id(_sid):  # type: ignore[no-redef]
        return None


def blog(message: str, level: str = "INFO") -> None:
    """Convenience wrapper. Mirrors the pattern in the original super_agent.py
    where every Node calls `broadcast_log(manager if BROADCASTING_AVAILABLE else None, ...)`."""
    broadcast_log(manager if BROADCASTING_AVAILABLE else None, message, level=level)


# --- Token usage tracker ----------------------------------------------------
# QoL phase — replaces the `len(text)//4` heuristic in _log_call. Nodes
# call record_tokens() after each LLM invoke; the API layer reads-and-resets
# at chat-end via pop_token_counts().
_TOKEN_COUNTS: dict = {"input": 0, "output": 0}


def record_tokens(input_tokens: int, output_tokens: int) -> None:
    """Add tokens to the per-process counter. Called by Node modules after
    each LLM invoke. Safe to call from sync or async contexts."""
    if input_tokens:
        _TOKEN_COUNTS["input"] += int(input_tokens)
    if output_tokens:
        _TOKEN_COUNTS["output"] += int(output_tokens)


def pop_token_counts() -> dict:
    """Return + reset the counter. Called by the API layer after each chat
    so tokens roll up per-session, not per-process-lifetime."""
    snap = {"input": _TOKEN_COUNTS["input"], "output": _TOKEN_COUNTS["output"]}
    _TOKEN_COUNTS["input"] = 0
    _TOKEN_COUNTS["output"] = 0
    return snap


def record_response_usage(response) -> None:
    """Pull usage_metadata off an LLM response and record it.

    LangChain v0.3+ exposes `response.usage_metadata = {input_tokens, output_tokens, total_tokens}`.
    Older shapes (response.response_metadata['usage']) are also handled.
    Best-effort — never raises into the agent flow.
    """
    if response is None:
        return
    try:
        meta = getattr(response, "usage_metadata", None)
        if isinstance(meta, dict) and meta:
            record_tokens(meta.get("input_tokens", 0), meta.get("output_tokens", 0))
            return
        rmeta = getattr(response, "response_metadata", None)
        if isinstance(rmeta, dict):
            usage = rmeta.get("usage") or {}
            record_tokens(
                usage.get("input_tokens", 0) or usage.get("prompt_tokens", 0),
                usage.get("output_tokens", 0) or usage.get("completion_tokens", 0),
            )
    except Exception:
        pass


# --- LLM factory (BYOK) -----------------------------------------------------
# P3.6 (B2): the testing agent no longer uses the platform LiteLLM key. Every
# LLM site builds its client from the run's tenant-resolved BYOK model, stashed
# in the model_resolver contextvar by the primary node before any tool/node LLM
# call. There is NO platform fallback — fail CLOSED when nothing is resolved.


def build_llm(*, max_tokens: int = 8192, **kwargs) -> ChatLiteLLM:
    """Build a ChatLiteLLM from the run's BYOK-resolved model.

    Reads the resolved model from the model_resolver contextvar (set by the
    testing graph's primary node). Fails CLOSED if no model has been resolved
    for this run — there is no platform-key fallback (spec D-4).

    Phase 1 mandated `max_tokens=8192` because `with_structured_output(TestPlan)`
    truncates without it — keep that default; callers may override.
    """
    from shared.services.model_resolver import get_resolved_model, temperature_kwargs

    resolved = get_resolved_model()
    if resolved is not None:
        # Only route through the LiteLLM proxy when the resolved model
        # explicitly configures a gateway `base_url` (e.g. an org-managed
        # gateway). Otherwise call the provider directly — mirrors every
        # other agent's build_llm and removes the hard dependency on a
        # locally-running LiteLLM proxy for direct-BYOK models (e.g. Anthropic).
        # temperature_kwargs, not a hardcoded value: the gpt-5 family accepts only the
        # default temperature and litellm raises UnsupportedParamsError BEFORE the call
        # rather than clamping, so every analysis call on a tenant running
        # azure/gpt-5-mini failed and the run reported "could not analyze the codebase"
        # — a model-parameter problem wearing the costume of an unreadable repo. Dev,
        # Requirements and Design already route through this helper.
        params = dict(
            model=resolved.model,
            custom_llm_provider=resolved.litellm_provider,
            api_base=resolved.base_url or None,
            api_key=resolved.api_key,
            max_retries=2,
            max_tokens=max_tokens,
            **temperature_kwargs(resolved.model, 0.2),
        )
        params.update(kwargs)
        # Deferred: importing litellm costs ~7s. sys.modules makes repeat calls free.
        from langchain_litellm import ChatLiteLLM
        return ChatLiteLLM(**params)

    # No resolved model in this context. The run resolves + stashes the model up
    # front, but each node re-enters asyncio in an executor thread, which can drop
    # the contextvar — so fall back here too. Enterprise still fails CLOSED; local
    # dev uses ANTHROPIC_API_KEY (mirrors every other agent's env fallback).
    from config.env import AGENT_RUNTIME_MODE
    if AGENT_RUNTIME_MODE == "enterprise" or not ANTHROPIC_API_KEY:
        raise RuntimeError(
            "No BYOK model resolved for this testing run. An administrator must "
            "configure and verify a model provider in Org Settings → Model Providers."
        )
    params = dict(
        model=ANTHROPIC_MODEL,
        custom_llm_provider="anthropic",
        api_key=ANTHROPIC_API_KEY,
        max_retries=2,
        max_tokens=max_tokens,
        **temperature_kwargs(ANTHROPIC_MODEL, 0.2),
    )
    params.update(kwargs)
    # Deferred: importing litellm costs ~7s. sys.modules makes repeat calls free.
    from langchain_litellm import ChatLiteLLM
    return ChatLiteLLM(**params)


def get_llm() -> ChatLiteLLM:
    """Per-call BYOK LLM client for the testing agent.

    Kept as the canonical name every Node imports. No longer a singleton — a
    fresh client is built per call from the run's resolved model so concurrent
    runs with different tenants never share a key.
    """
    return build_llm(max_tokens=8192)


# --- File-generation broadcaster --------------------------------------------
async def broadcast_file_generated(session_id: str, filename: str, file_path: str) -> None:
    """Broadcast a file_generated event with the file's size.

    Path layout preserved exactly from canonical super_agent.py line 77:
    `{AGENTIC_BASE_URL}/generated/{user_id}/orchestrator/{session_id}/output/{filename}`.
    Phase 4 keeps the hardcoded `orchestrator` segment so URLs don't change.
    """
    if not BROADCASTING_AVAILABLE:
        print(f"File generated: {filename} at {file_path}")
        return

    try:
        user_id = get_user_id()
        file_size = os.path.getsize(file_path) if os.path.exists(file_path) else 0
        file_url = f"{AGENTIC_BASE_URL}/generated/{user_id}/orchestrator/{session_id}/output/{filename}"
        await manager.broadcast({
            "type": "file_generated",
            "session_id": session_id,
            "filename": filename,
            "url": file_url,
            "file_size": file_size,
            "message": f"Generated file: {filename}",
        })
        print(f"DEBUG: Broadcasted file generation: {filename} ({file_size} bytes)")
    except Exception as exc:
        print(f"ERROR: Failed to broadcast file generation: {exc}")
