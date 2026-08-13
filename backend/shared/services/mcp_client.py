"""MCP client — connects to user-registered MCP servers and returns LangChain tools.

Built on langchain-mcp-adapters' MultiServerMCPClient, which yields native LangChain
tools that drop straight into an agent's bind_tools / ToolNode flow (see
shared/tools/mcp_runtime.py). This module is deliberately free of DB / secret-store
coupling: callers resolve registry rows + decrypt credentials and pass plain configs,
so it stays unit-testable against any MCP server.

Transports supported: streamable_http (primary, remote), sse (legacy remote), stdio
(local subprocess — gated by MCP_STDIO_ENABLED + command allowlist).

Bounded by MCP_CONNECT_TIMEOUT_SECONDS / MCP_TOOL_TIMEOUT_SECONDS so a slow or
unreachable server can never hang a stage.
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Optional, TypedDict

from config.env import (
    MCP_CONNECT_TIMEOUT_SECONDS,
    MCP_STDIO_COMMAND_ALLOWLIST,
    MCP_STDIO_ENABLED,
    MCP_TOOL_TIMEOUT_SECONDS,
)

logger = logging.getLogger(__name__)

_VALID_TRANSPORTS = {"streamable_http", "sse", "stdio"}
_NAME_SANITIZE = re.compile(r"[^a-zA-Z0-9_]+")


class ServerConfig(TypedDict, total=False):
    """A resolved MCP server connection (secrets already decrypted by the caller)."""
    id: str                    # mcp_servers.id — stable identifier consumed by Task 10
    name: str            # server_name — namespacing prefix + MultiServerMCPClient key
    transport: str       # "streamable_http" | "sse" | "stdio"
    url: Optional[str]         # http/sse
    command: Optional[str]     # stdio
    args: Optional[list]       # stdio
    headers: Optional[dict]    # http/sse — decrypted
    env: Optional[dict]        # stdio — decrypted
    capabilities: list         # admin-asserted BYO capability tags (DP2)


class McpConfigError(ValueError):
    """Raised when a server config is invalid or a transport is not permitted."""


def _safe_prefix(name: str) -> str:
    return _NAME_SANITIZE.sub("_", (name or "mcp").strip()).strip("_") or "mcp"


def _to_connection(cfg: ServerConfig) -> dict[str, Any]:
    """Translate a registry server config into a MultiServerMCPClient connection dict.

    Validates transport and enforces the stdio guard (subprocess on the backend host).
    """
    transport = (cfg.get("transport") or "").strip()
    if transport not in _VALID_TRANSPORTS:
        raise McpConfigError(
            f"Unsupported MCP transport {transport!r}; expected one of {sorted(_VALID_TRANSPORTS)}"
        )

    if transport in ("streamable_http", "sse"):
        url = cfg.get("url")
        if not url:
            raise McpConfigError(f"MCP transport {transport!r} requires a url")
        conn: dict[str, Any] = {"transport": transport, "url": url}
        if cfg.get("headers"):
            conn["headers"] = dict(cfg["headers"])
        return conn

    # stdio — spawns a subprocess; double-gated by flag + command allowlist.
    if not MCP_STDIO_ENABLED:
        raise McpConfigError("stdio MCP transport is disabled (MCP_STDIO_ENABLED=false)")
    command = (cfg.get("command") or "").strip()
    if not command:
        raise McpConfigError("MCP stdio transport requires a command")
    if command not in MCP_STDIO_COMMAND_ALLOWLIST:
        raise McpConfigError(
            f"MCP stdio command {command!r} is not in MCP_STDIO_COMMAND_ALLOWLIST"
        )
    conn = {"transport": "stdio", "command": command, "args": list(cfg.get("args") or [])}
    if cfg.get("env"):
        conn["env"] = dict(cfg["env"])
    return conn


def _wrap_timeout(tool):
    """Bound a tool's async call so a hung MCP server surfaces an error, not a stall."""
    orig = getattr(tool, "coroutine", None)
    if orig is None:
        return tool

    async def _bounded(*args, **kwargs):
        return await asyncio.wait_for(orig(*args, **kwargs), timeout=MCP_TOOL_TIMEOUT_SECONDS)

    tool.coroutine = _bounded
    return tool


def _namespace(tool, prefix: str):
    """Prefix the tool name with its server to avoid collisions with base agent tools."""
    try:
        tool.name = f"{prefix}__{tool.name}"
    except Exception:
        pass
    return tool


async def load_tools(server_configs: list[ServerConfig]) -> list:
    """Connect to each server and return combined, namespaced, timeout-bounded tools.

    A failure to connect to one server is logged and skipped (graceful degradation) —
    one broken MCP server must not fail an entire pipeline stage.
    """
    if not server_configs:
        return []

    from langchain_mcp_adapters.client import MultiServerMCPClient

    out: list = []
    for cfg in server_configs:
        name = cfg.get("name") or "mcp"
        try:
            connection = _to_connection(cfg)
            client = MultiServerMCPClient({name: connection})
            tools = await asyncio.wait_for(
                client.get_tools(server_name=name), timeout=MCP_CONNECT_TIMEOUT_SECONDS
            )
            prefix = _safe_prefix(name)
            out.extend(_wrap_timeout(_namespace(t, prefix)) for t in tools)
        except Exception as exc:  # noqa: BLE001 — degrade gracefully, never crash the stage
            logger.warning("MCP server %r unavailable, skipping its tools: %s", name, exc)
    return out


async def test_connection(cfg: ServerConfig) -> dict[str, Any]:
    """Connect to one server and list its tools — for the register/probe UX.

    Returns {ok, tools:[{name, description}], error}. Never raises.
    """
    from langchain_mcp_adapters.client import MultiServerMCPClient

    name = cfg.get("name") or "mcp"
    try:
        connection = _to_connection(cfg)
        client = MultiServerMCPClient({name: connection})
        tools = await asyncio.wait_for(
            client.get_tools(server_name=name), timeout=MCP_CONNECT_TIMEOUT_SECONDS
        )
        return {
            "ok": True,
            "tools": [{"name": t.name, "description": (t.description or "")[:500]} for t in tools],
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "tools": [], "error": str(exc)}
