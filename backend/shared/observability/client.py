"""Langfuse client singleton — process-wide, env-driven, fail-open.

get_langfuse_client() returns a configured Langfuse instance when ENABLE_LANGFUSE
is true AND both keys are present; otherwise None. Every caller treats None as
"tracing disabled" and no-ops, so a missing/misconfigured Langfuse never breaks an
agent run (parity with the fire-and-forget audit/cost services).

The langfuse SDK (v3) is OpenTelemetry-based: the LangChain CallbackHandler resolves
this same singleton via langfuse.get_client(), so it must be constructed before any
handler is created. Constructing Langfuse() here (rather than relying purely on env)
keeps the keys flowing through config.env — the single source of truth — instead of
duplicating os.environ reads inside the SDK.
"""
from __future__ import annotations

import logging
import threading
from typing import Optional

from config.env import (
    ENABLE_LANGFUSE,
    LANGFUSE_HOST,
    LANGFUSE_PUBLIC_KEY,
    LANGFUSE_SECRET_KEY,
)

logger = logging.getLogger(__name__)

_client = None
_init_attempted = False
_lock = threading.Lock()


def get_langfuse_client():
    """Return the process Langfuse singleton, or None when tracing is disabled.

    Thread-safe, memoised. A construction failure is logged once and cached as
    None so a broken config degrades to "no tracing" rather than raising on every
    agent turn.
    """
    global _client, _init_attempted
    if _init_attempted:
        return _client
    with _lock:
        if _init_attempted:
            return _client
        _init_attempted = True
        if not (ENABLE_LANGFUSE and LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY):
            _client = None
            return None
        try:
            from langfuse import Langfuse  # noqa: PLC0415

            _client = Langfuse(
                public_key=LANGFUSE_PUBLIC_KEY,
                secret_key=LANGFUSE_SECRET_KEY,
                host=LANGFUSE_HOST,
            )
            logger.info("Langfuse tracing enabled: host=%s", LANGFUSE_HOST)
        except Exception:  # pragma: no cover - defensive
            logger.warning("Langfuse client init failed; tracing disabled", exc_info=True)
            _client = None
        return _client


def flush_langfuse() -> None:
    """Best-effort flush of buffered spans. No-op when tracing is disabled."""
    client = get_langfuse_client()
    if client is None:
        return
    try:
        client.flush()
    except Exception:  # pragma: no cover - defensive
        logger.debug("Langfuse flush failed (swallowed)", exc_info=True)
