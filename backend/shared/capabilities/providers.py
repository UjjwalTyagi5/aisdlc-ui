"""A candidate capability provider gathered from one of the three tiers (DP4)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


@dataclass(frozen=True)
class CapabilityProvider:
    tier: str                 # "native" | "curated" | "byo"
    capability: str
    ref: str                  # tool name (native), catalog key (curated), server id (byo)
    tool: Optional[Any] = None  # the bindable LangChain tool, when available


from shared.capabilities import native_tags, system_catalog  # noqa: E402


def gather_native(agent_id: str, tools) -> "list[CapabilityProvider]":
    tag_map = native_tags.NATIVE_TAGS.get(agent_id, {})
    out: list[CapabilityProvider] = []
    for t in tools or []:
        name = getattr(t, "name", None)
        value = tag_map.get(name)
        if not value:
            continue
        # A tool may provide several capabilities — emit one provider per cap.
        for cap in native_tags._as_caps(value):
            out.append(CapabilityProvider(tier="native", capability=cap, ref=name, tool=t))
    return out


def gather_curated(agent_id: str, disabled, curated_tools_by_key: dict) -> "list[CapabilityProvider]":
    """curated_tools_by_key maps CuratedTool.key -> bound LangChain tool (when the
    managed MCP server is reachable). Missing key => provider with tool=None, which
    still counts for the capability-gap check but binds nothing (DP: dev tolerance)."""
    out: list[CapabilityProvider] = []
    for ct in system_catalog.curated_for_agent(agent_id, disabled=set(disabled or ())):
        out.append(CapabilityProvider(
            tier="curated", capability=ct.capability, ref=ct.key,
            tool=curated_tools_by_key.get(ct.key),
        ))
    return out


def gather_byo(server_rows, mcp_tools) -> "list[CapabilityProvider]":
    """server_rows: list of dicts with id + capabilities (from resolve_server_configs).
    mcp_tools: the already-loaded BYO LangChain tools (namespaced). For v1 (DP2) the
    capability tag is per-server; we attach the first matching loaded tool via
    _byo_server_id if present, else tool=None (counts for the gap check)."""
    out: list[CapabilityProvider] = []
    tools_by_server = {}
    for t in mcp_tools or []:
        sid = getattr(t, "_byo_server_id", None)
        if sid:
            tools_by_server.setdefault(str(sid), []).append(t)
    for row in server_rows or []:
        sid = str(row.get("id"))
        bound = tools_by_server.get(sid) or [None]
        for cap in (row.get("capabilities") or []):
            out.append(CapabilityProvider(tier="byo", capability=cap, ref=sid, tool=bound[0]))
    return out
