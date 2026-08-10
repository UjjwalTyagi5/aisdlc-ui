"""shared.observability — Langfuse LLM tracing (complements audit/cost/Prometheus).

Exports:
  get_langfuse_client — process Langfuse singleton, or None when disabled
  flush_langfuse      — best-effort flush of buffered spans
  build_agent_callbacks — the single attach point for agent LLM callbacks
                          (AuditCallbackHandler + optional Langfuse handler + trace CM)
"""
from __future__ import annotations

from shared.observability.callbacks import (
    build_agent_callbacks,
    langfuse_langchain_extras,
)
from shared.observability.client import flush_langfuse, get_langfuse_client

__all__ = [
    "build_agent_callbacks",
    "langfuse_langchain_extras",
    "flush_langfuse",
    "get_langfuse_client",
]
