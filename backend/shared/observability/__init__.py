"""shared.observability — Langfuse LLM tracing (complements audit/cost/Prometheus).

Exports:
  get_langfuse_client — process Langfuse singleton, or None when disabled
  flush_langfuse      — best-effort flush of buffered spans
  agent_trace         — THE attach point for an agent route: resolves identity
                        (tenant/user/workspace) then builds the callbacks
  langfuse_langchain_extras — the underlying builder; prefer agent_trace
"""
from __future__ import annotations

from shared.observability.callbacks import agent_trace, langfuse_langchain_extras
from shared.observability.client import flush_langfuse, get_langfuse_client

__all__ = [
    "agent_trace",
    "langfuse_langchain_extras",
    "flush_langfuse",
    "get_langfuse_client",
]
